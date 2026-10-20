"""Post-match condition evaluation, intentionally separate from the identity scorer.

Classification priority: accepted duplicate > triggered review > clean. In particular,
custom flags never exclude a row from candidate generation. Only matching engine's
existing identity/data-quality guards are in effect during matching.
"""
from __future__ import annotations
import calendar
import json
import re
import sqlite3
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from engine import crossmatch_engine as eng
from services.header_detector import CORE

OPERATORS = {'equals','not_equals','contains','is_any_of','is_blank','is_not_blank',
             'greater_than','less_than','before_date','after_date','within_last_months',
             'older_than_months','future_date','invalid_date'}
CROSS_OPERATORS = {'equals','not_equals','contains','greater_than','less_than','before_date','after_date'}
CROSS_VALUE_OPERATORS = {'none'} | CROSS_OPERATORS
ACTIONS = {'flag_review','note_only','validated_records','duplicate_records','records_for_review'}
BUILTINS = ('deceased','status_conflict','waived','unlocated','unknown','transfer_residence',
            'for_verification','missing_first','missing_last','missing_birthdate','incomplete_date',
            'birthdate_conflict','age_limit','old_accomplished','cross_status_conflict')
DEFAULT_BUILTINS = dict.fromkeys(BUILTINS, False)  # custom-only UI: no invisible automatic conditions
DATE_FORMATS = ('%Y-%m-%d','%Y/%m/%d','%m/%d/%Y','%m-%d-%Y','%m/%d/%y','%d %b %Y','%d %B %Y')


def basic(v):
    return re.sub(r'\s+',' ', re.sub(r'[^\w\s]+',' ',str(v or '').casefold())).strip()

def missing(v): return basic(v) in ('','nan','na','n a','null','none','not applicable')

@lru_cache(maxsize=16000)
def parse_date(raw):
    raw=str(raw or '').strip()
    if not raw or missing(raw): return None
    for fmt in DATE_FORMATS:
        try: return datetime.strptime(raw,fmt).date()
        except ValueError: pass
    # Real XLSX date cells are staged as ISO datetimes (for example,
    # "2025-01-20 00:00:00"), even if Excel displays only "01/20/2025".
    # The condition evaluator must interpret these as dates, not silently
    # ignore before/after, recent-month, future and invalid-date rules.
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00')).date()
    except ValueError:
        pass
    # CSV exports often carry dates in the user's locale plus a time stamp.
    for fmt in ('%m/%d/%Y %H:%M:%S', '%m-%d-%Y %H:%M:%S',
                '%m/%d/%Y %H:%M', '%m-%d-%Y %H:%M'):
        try: return datetime.strptime(raw, fmt).date()
        except ValueError: pass
    return None

def subtract_calendar_months(ref, months=3):
    m=ref.year*12+(ref.month-1)-months
    y, mo=divmod(m,12);mo+=1
    return date(y,mo,min(ref.day,calendar.monthrange(y,mo)[1]))


def standard_config():
    return {'enabled':True,'builtins':dict(DEFAULT_BUILTINS),'rules':[],
            'reference_date':date.today().isoformat(),'header_high':95,'header_suggest':85}


def validated_config(data, headers_a, headers_b=None, mapping_a=None, mapping_b=None):
    if not isinstance(data,dict): raise ValueError('Conditions must be an object.')
    result=standard_config();result['enabled']=bool(data.get('enabled',True))
    builtins=data.get('builtins',{})
    if not isinstance(builtins,dict): raise ValueError('Built-in conditions must be an object.')
    # Former built-in rules were removed from the user interface. Legacy saved
    # profiles may still contain their flags; ignore them rather than silently
    # applying invisible REVIEW rules. Only explicit custom rules can flag rows.
    result['builtins']={k:False for k in BUILTINS}
    ref=str(data.get('reference_date') or date.today().isoformat())
    if parse_date(ref) is None: raise ValueError('Reference date must be valid YYYY-MM-DD.')
    result['reference_date']=ref
    result['header_high']=max(90,min(100,int(data.get('header_high',95))))
    result['header_suggest']=max(60,min(result['header_high']-1,int(data.get('header_suggest',85))))
    rules=data.get('rules',[])
    if not isinstance(rules,list) or len(rules)>40: raise ValueError('Use at most 40 custom rules.')
    result['rules']=[]
    for i,r in enumerate(rules):
        if not isinstance(r,dict): raise ValueError('Invalid rule format.')
        source=str(r.get('source','a')).lower()
        enabled=bool(r.get('enabled',True))
        if source not in ('a','b','cross'):
            raise ValueError(f'Rule {i+1}: choose File A, File B, or File A versus File B.')
        if enabled and source in ('b','cross') and not headers_b:
            raise ValueError(f'Rule {i+1}: File A vs File B conditions require Cross Match mode and uploaded File B.')
        header=str(r.get('header',''))
        compare_header=str(r.get('compare_header','')) if source=='cross' else ''
        selected=list(headers_b or []) if source=='b' else list(headers_a)
        if header not in selected and enabled:
            raise ValueError(f'Rule {i+1}: selected source header is not in File {"B" if source=="b" else "A"}.')
        if source=='cross' and enabled and compare_header not in list(headers_b or []):
            raise ValueError(f'Rule {i+1}: select a valid File B comparison header.')
        def is_ignored(mapping, physical_header):
            if not isinstance(mapping,dict):return False
            ignored=set(mapping.get('exact_ignored_fields',[]))|set(mapping.get('ignored_fields',[]))|set(mapping.get('ignored_headers',[]))
            if physical_header in ignored:return True
            return any(mapping.get(k)==physical_header and k in ignored for k in CORE)
        left_map=mapping_b if source=='b' else mapping_a
        if enabled and is_ignored(left_map,header):
            raise ValueError(f'Rule {i+1}: source header is marked Ignore.')
        if source=='cross' and enabled and is_ignored(mapping_b,compare_header):
            raise ValueError(f'Rule {i+1}: File B comparison header is marked Ignore.')
        op=str(r.get('operator','equals'))
        if op not in (CROSS_OPERATORS if source=='cross' else OPERATORS):
            raise ValueError(f'Rule {i+1}: unsupported operator for the selected condition type.')
        action=str(r.get('action','records_for_review'))
        if action not in ACTIONS: raise ValueError(f'Rule {i+1}: unsupported action.')
        value=str(r.get('value',''))[:500]
        value_operator=''
        if source=='cross':
            # Backward compatibility: v6.32.16-v6.32.18 stored a nonblank cross value
            # as an exact File A filter. Treat that legacy value as Equals.
            value_operator=str(r.get('value_operator') or ('equals' if value.strip() else 'none')).lower()
            if value_operator not in CROSS_VALUE_OPERATORS:
                raise ValueError(f'Rule {i+1}: unsupported optional File A check.')
            if enabled and value_operator!='none' and not value.strip():
                raise ValueError(f'Rule {i+1}: enter the optional File A value or choose No extra File A check.')
            if enabled and value_operator in ('greater_than','less_than') and value.strip():
                try: float(value)
                except (TypeError,ValueError): raise ValueError(f'Rule {i+1}: enter a valid number for the optional File A check.')
            if enabled and value_operator in ('before_date','after_date') and value.strip() and parse_date(value) is None:
                raise ValueError(f'Rule {i+1}: choose a valid date for the optional File A check.')
        no_value_ops=('is_blank','is_not_blank','future_date','invalid_date')
        if enabled and source!='cross' and op not in no_value_ops and not value.strip():
            raise ValueError(f'Rule {i+1}: enter the value, text, number, date, or months required by the selected operator.')
        if enabled and source!='cross' and op in ('before_date','after_date') and parse_date(value) is None:
            raise ValueError(f'Rule {i+1}: invalid comparison date; choose a valid date.')
        if enabled and source!='cross' and op in ('greater_than','less_than'):
            try: float(value)
            except (TypeError,ValueError): raise ValueError(f'Rule {i+1}: enter a valid number.')
        if enabled and source!='cross' and op in ('within_last_months','older_than_months'):
            try:
                months=int(value)
            except (TypeError,ValueError):
                raise ValueError(f'Rule {i+1}: number of months must be a whole number.')
            if months < 1 or months > 120:
                raise ValueError(f'Rule {i+1}: number of months must be between 1 and 120.')
            value=str(months)
        name=str(r.get('name') or f'Rule {i+1}')[:65].strip()
        result['rules'].append(dict(name=name,source=source,header=header,
            compare_header=compare_header,operator=op,value=value,value_operator=value_operator,action=action,
            priority=max(1,min(999,int(r.get('priority',100)))),
            enabled=enabled,group=str(r.get('group','1'))[:16],
            join=str(r.get('join','AND')).upper() if str(r.get('join','AND')).upper() in ('AND','OR') else 'AND'))
    group_scopes = {}
    for index, rule in enumerate(result['rules']):
        if not rule['enabled']:
            continue
        scope = 'reference' if rule['source'] == 'b' else 'main'
        previous = group_scopes.setdefault(rule['group'], scope)
        if previous != scope:
            raise ValueError(f'Rule {index+1}: put File B-only checks in a separate group from File A or A/B checks.')
    return result


def mapped_header(mapping, meaning):
    if not mapping:return ''
    if meaning in CORE:
        value=getattr(mapping,meaning,'')
        return value if value and not mapping.ignore_enabled(meaning) else ''
    for header, semantic in (mapping.custom_meanings or {}).items():
        if semantic==meaning and header not in mapping.exact_ignored_fields:
            return header
    return ''


def string_col(frame,header):
    if not header or header not in frame.columns:
        return pd.Series('',index=frame.index,dtype='string')
    return frame[header].astype('string').fillna('').str.strip()

def normalized(series):
    return (series.astype('string').fillna('').str.casefold()
          .str.replace(r'[^\w\s]+',' ',regex=True).str.replace(r'\s+',' ',regex=True).str.strip())

def mask_for_rule(frame,rule,reference_date=None):
    raw=string_col(frame,rule['header']);vals=normalized(raw);op=rule['operator'];value=basic(rule['value'])
    if op=='equals':return vals.eq(value).fillna(False)
    if op=='not_equals':return vals.ne(value).fillna(False)
    if op=='contains':return vals.str.contains(re.escape(value),regex=True,na=False)
    if op=='is_any_of':
        terms={basic(x) for x in rule['value'].split(',') if basic(x)}
        return vals.isin(terms)
    blank=vals.isin({'','nan','na','n a','null','none','not applicable'})
    if op=='is_blank':return blank
    if op=='is_not_blank':return ~blank
    if op in ('greater_than','less_than'):
        numeric=pd.to_numeric(raw,errors='coerce');num=float(rule['value'])
        return (numeric>num if op=='greater_than' else numeric<num).fillna(False)
    if op in ('before_date','after_date'):
        threshold=parse_date(rule['value'])
        dates=raw.map(parse_date)
        return dates.map(lambda d:bool(d and (d<threshold if op=='before_date' else d>threshold)))
    if op in ('within_last_months','older_than_months'):
        ref=parse_date(reference_date) if reference_date else date.today()
        months=int(rule['value'])
        cutoff=subtract_calendar_months(ref,months)
        dates=raw.map(parse_date)
        if op=='within_last_months':
            # Inclusive calendar-month boundary. Future dates are not considered
            # recent; use Future Date as a separate validation condition.
            return dates.map(lambda d:bool(d and cutoff<=d<=ref))
        return dates.map(lambda d:bool(d and d<cutoff))
    if op=='future_date':
        ref=parse_date(reference_date) if reference_date else date.today()
        dates=raw.map(parse_date)
        return dates.map(lambda d:bool(d and d>ref))
    if op=='invalid_date':
        blank=blank_mask(raw)
        parsed=raw.map(parse_date)
        return (~blank & parsed.isna()).fillna(False)
    raise ValueError('Unknown operator')


def blank_mask(series): return normalized(series).isin({'','nan','na','n a','null','none','not applicable'})


def mask_for_cross_rule(frame_a, aligned_b, rule, matched_mask):
    """Compare aligned File A and File B columns, only for accepted A/B pairs.

    This is a *post-match* validation. Both missing values and unmatched File A
    records are explicitly excluded; use an individual Is Blank rule instead.
    """
    n=len(frame_a)
    if aligned_b is None or n==0:
        return np.zeros(n,dtype=bool)
    left=string_col(frame_a,rule['header'])
    right=string_col(aligned_b,rule['compare_header'])
    allowed=(np.asarray(matched_mask,dtype=bool)
             & ~np.asarray(blank_mask(left),dtype=bool)
             & ~np.asarray(blank_mask(right),dtype=bool))
    op=rule['operator']
    if op in ('equals','not_equals','contains'):
        a=normalized(left);b=normalized(right)
        if op=='equals':result=a.eq(b).fillna(False).to_numpy(dtype=bool)
        elif op=='not_equals':result=a.ne(b).fillna(False).to_numpy(dtype=bool)
        else:
            # Contains has a different RHS per row, so iterate only the
            # selected matched candidates, not a quadratic file-wide join.
            result=np.zeros(n,dtype=bool)
            for i in np.flatnonzero(allowed):
                result[i]=bool(b.iloc[i] in a.iloc[i])
    elif op in ('greater_than','less_than'):
        a=pd.to_numeric(left,errors='coerce');b=pd.to_numeric(right,errors='coerce')
        result=(a.gt(b) if op=='greater_than' else a.lt(b)).fillna(False).to_numpy(dtype=bool)
    elif op in ('before_date','after_date'):
        # Only matched rows with nonblank dates need parsing. Cache parse_date
        # already avoids repeated conversion for common date values.
        result=np.zeros(n,dtype=bool)
        for i in np.flatnonzero(allowed):
            a=parse_date(left.iloc[i]);b=parse_date(right.iloc[i]);
            if a and b:result[i]=(a<b if op=='before_date' else a>b)
    else:raise ValueError('Unsupported File A vs File B comparison')
    result = result & allowed
    # Optional extra File A check. Blank/none keeps the normal A ↔ B comparison.
    # Legacy profiles with a nonblank value and no value_operator retain their
    # old exact-match behavior by defaulting to Equals.
    raw_value=str(rule.get('value','') or '').strip()
    extra_op=str(rule.get('value_operator') or ('equals' if raw_value else 'none')).lower()
    if raw_value and extra_op!='none':
        if extra_op in ('equals','not_equals','contains'):
            a=normalized(left);typed=basic(raw_value)
            if extra_op=='equals': extra=a.eq(typed).fillna(False).to_numpy(dtype=bool)
            elif extra_op=='not_equals': extra=a.ne(typed).fillna(False).to_numpy(dtype=bool)
            else: extra=a.str.contains(typed,regex=False,na=False).to_numpy(dtype=bool)
        elif extra_op in ('greater_than','less_than'):
            a=pd.to_numeric(left,errors='coerce');typed=float(raw_value)
            extra=(a.gt(typed) if extra_op=='greater_than' else a.lt(typed)).fillna(False).to_numpy(dtype=bool)
        elif extra_op in ('before_date','after_date'):
            typed=parse_date(raw_value);extra=np.zeros(n,dtype=bool)
            if typed:
                for i in np.flatnonzero(allowed):
                    a=parse_date(left.iloc[i])
                    if a: extra[i]=(a<typed if extra_op=='before_date' else a>typed)
        else:
            raise ValueError('Unsupported optional File A comparison')
        result = result & extra
    return result


def evaluate_frame(frame,mapping,config,source='a',other_status=None, min_age=0,max_age=0,
                   aligned_reference=None, matched_mask=None):
    """Return sparse dictionaries indexed by *chunk-local* integer, never patient details in logs."""
    if frame.empty:return {}
    n=len(frame);result={}
    if not config.get('enabled',True):return result
    built=config['builtins'];status_h=mapped_header(mapping,'death_status');case_h=mapped_header(mapping,'case_status')
    dod_h=mapped_header(mapping,'date_of_death');dates_h=mapped_header(mapping,'date_accomplished')
    approved=set(config.get('review_'+source,[]))
    def active(header): return bool(header and header in approved)
    ds=normalized(string_col(frame,status_h));cs=normalized(string_col(frame,case_h));dod=string_col(frame,dod_h)
    def add(mask,reason):
        arr=np.flatnonzero(np.asarray(mask,dtype=bool))
        for pos in arr:
            result.setdefault(int(pos),[]).append(reason)
    if source in ('a','b'):
        if built.get('deceased') and active(status_h):add(ds.isin({'deceased','dead','died'}),'Deceased')
        if built.get('status_conflict') and active(status_h) and active(dod_h):
            add(ds.isin({'alive','living','not deceased'}) & ~blank_mask(dod),'STATUS CONFLICT: alive with date of death')
        statuses={'waived':{'waived','waive','waiver'},'unlocated':{'unlocated','cannot locate'},
                  'unknown':{'unknown'},'transfer_residence':{'transfer of residence','transferred residence','transferred'},
                  'for_verification':{'for verification','pending verification'}}
        if active(case_h):
            for flag,words in statuses.items():
                if built.get(flag): add(cs.isin(words),flag.replace('_',' ').title())
    if source=='a':
        first=mapped_header(mapping,'first');last=mapped_header(mapping,'last')
        bd=mapped_header(mapping,'birthdate');bm=mapped_header(mapping,'birth_month');
        bday=mapped_header(mapping,'birth_day');by=mapped_header(mapping,'birth_year')
        if built.get('missing_first') and active(first): add(blank_mask(string_col(frame,first)),'Missing first name')
        if built.get('missing_last') and active(last): add(blank_mask(string_col(frame,last)),'Missing last name')
        if built.get('missing_birthdate'):
            if active(bd): add(blank_mask(string_col(frame,bd)),'Missing birthdate')
            elif active(bm) and active(bday) and active(by):
                add(blank_mask(string_col(frame,bm))|blank_mask(string_col(frame,bday))|blank_mask(string_col(frame,by)),'Incomplete birthdate')
        if built.get('incomplete_date') and active(bd):
            col=string_col(frame,bd);add(~blank_mask(col)&col.map(parse_date).isna(),'Invalid/incomplete birthdate')
        if built.get('old_accomplished') and active(dates_h):
            cutoff=subtract_calendar_months(parse_date(config['reference_date']),3)
            parsed=string_col(frame,dates_h).map(parse_date)
            add(parsed.map(lambda d:bool(d and d<cutoff)),'Date accomplished older than 3 calendar months')
        # Age bounds in the core matcher remain configured independently; do not silently
        # add another age gate in the condition builder unless the toggle is selected.
        if built.get('age_limit') and (active(bd) or (active(bm) and active(bday) and active(by))):
            if bd: parsed=string_col(frame,bd).map(parse_date)
            else:
                raw=(string_col(frame,bm)+'/'+string_col(frame,bday)+'/'+string_col(frame,by))
                parsed=raw.map(parse_date)
            ref=parse_date(config['reference_date'])
            def outside_age(d):
                if not d:return False
                age=ref.year-d.year-((ref.month,ref.day)<(d.month,d.day))
                return bool((min_age and age<min_age) or (max_age and age>max_age))
            add(parsed.map(outside_age),'Outside selected age limits')
    # Consecutive rules in a named group combine with AND/OR. Groups are OR-ed.
    groups={}
    for r in sorted(config.get('rules',[]),key=lambda x:x['priority']):
        if not r['enabled']:continue
        if r['source']==source:
            mask=mask_for_rule(frame,r,config.get('reference_date')).to_numpy(dtype=bool)
        elif source=='a' and r['source']=='cross':
            mask=mask_for_cross_rule(frame,aligned_reference,r,
                matched_mask if matched_mask is not None else np.zeros(n,dtype=bool))
        else:continue
        grp=groups.setdefault(r['group'],[]);grp.append((r,mask))
    for grp,predicates in groups.items():
        combined=predicates[0][1].copy()
        for r,mask in predicates[1:]:
            if r['join']=='OR':combined|=mask
            else:combined&=mask
        if combined.any():
            label=' + '.join((
                f"{r['name']} (File A: {r['header']} vs File B: {r['compare_header']}"
                + (f"; File A {str(r.get('value_operator') or 'equals').replace('_', ' ')}: {r['value']}"
                   if str(r.get('value','')).strip() and r.get('value_operator') != 'none' else '') + ")"
                if r['source']=='cross' else r['name']) for r,_ in predicates)
            # A group is one predicate. REVIEW takes precedence over duplicate request,
            # then validated/note-only. Individual group actions never create matches.
            actions={r['action'] for r,_ in predicates}
            if 'records_for_review' in actions: action='records_for_review'
            elif 'flag_review' in actions: action='flag_review'
            elif 'duplicate_records' in actions: action='duplicate_records'
            elif 'validated_records' in actions: action='validated_records'
            else: action='note_only'
            prefixes={
                'note_only':'NOTE: ', 'flag_review':'',
                'validated_records':'VALIDATED RECORDS: ',
                'duplicate_records':'DUPLICATE RECORDS: ',
                'records_for_review':'RECORDS FOR REVIEW: ',
            }
            for pos in np.flatnonzero(combined):
                result.setdefault(int(pos),[]).append(prefixes[action]+label)
    return result


def _staged_condition_chunks(staged, chunk_rows=10000):
    """Reuse already validated staged records; never reopen the full XLSX.

    Excel's intermediate raw_csv was produced by stage_input after dropping
    only blank and all-placeholder rows. Reading it preserves staging row IDs.
    For a real source CSV, use the original iterator so the same skip policy
    applies before the advanced-condition pass.
    """
    if staged.raw_csv == staged.source_plan.path and staged.source_plan.path.suffix.casefold() == '.csv':
        return eng.iter_input_chunks(staged.source_plan, chunk_rows)
    return pd.read_csv(
        staged.raw_csv, dtype=str, encoding='utf-8-sig',
        keep_default_na=False, na_filter=False, chunksize=chunk_rows,
        low_memory=False,
    )


def _selected_b_rows(b, matched_ids, mapping_b, config, conn, log):
    if b is None or len(matched_ids)==0:return
    sql='INSERT OR REPLACE INTO b_selected(row_id,payload,death_status,case_status,dod,notes) VALUES(?,?,?,?,?,?)'
    offset=0;inserted=0
    for chunk in _staged_condition_chunks(b,10000):
        n=len(chunk)
        low=np.searchsorted(matched_ids,offset,side='left');high=np.searchsorted(matched_ids,offset+n,side='left')
        ids=matched_ids[low:high]
        if ids.size:
            positions=(ids-offset).astype(int)
            selected=chunk.iloc[positions].reset_index(drop=True)
            flags=evaluate_frame(selected,mapping_b,config,source='b')
            dsh=mapped_header(mapping_b,'death_status');csh=mapped_header(mapping_b,'case_status');dodh=mapped_header(mapping_b,'date_of_death')
            rows=[]
            for k,row in enumerate(selected.itertuples(index=False,name=None)):
                values=[str(x or '') for x in row]
                rows.append((int(ids[k]),json.dumps(values,ensure_ascii=False),
                    values[b.headers.index(dsh)] if dsh else '',values[b.headers.index(csh)] if csh else '',
                    values[b.headers.index(dodh)] if dodh else '', '; '.join(flags.get(k,[]))))
            conn.executemany(sql,rows);inserted+=len(rows);conn.commit()
        offset+=n
    if offset!=b.rows or inserted!=len(matched_ids):
        raise RuntimeError(f'File B condition alignment mismatch: {inserted}/{len(matched_ids)} selected reference rows.')
    log(f'Condition evaluation: prepared {inserted:,} matched File B reference row(s).')


def apply_conditions(a,b,class_paths,config,options,log,temp_root):
    """Pass performed *after* identity matching. Sparse findings and B pairs are disk-backed."""
    db=Path(temp_root)/'advanced_conditions.sqlite'
    conn=sqlite3.connect(db)
    conn.execute('PRAGMA journal_mode=OFF')
    conn.execute('CREATE TABLE flags_a(row_id INTEGER PRIMARY KEY, notes TEXT NOT NULL, death_a TEXT, death_b TEXT, flag_review INTEGER NOT NULL)')
    conn.execute('CREATE TABLE b_selected(row_id INTEGER PRIMARY KEY,payload TEXT NOT NULL,death_status TEXT,case_status TEXT,dod TEXT,notes TEXT)')
    types=np.memmap(class_paths['type'],dtype=np.uint8,mode='r+',shape=(a.rows,))
    matched=np.memmap(class_paths['matched'],dtype=np.int64,mode='r',shape=(a.rows,))
    matches=np.unique(np.asarray(matched)[matched>=0]) if b is not None else np.empty(0,dtype=np.int64)
    _selected_b_rows(b,matches,b.mapping if b is not None else None,config,conn,log)
    offset=0;triggered=0;promoted=0
    sql='INSERT INTO flags_a(row_id,notes,death_a,death_b,flag_review) VALUES(?,?,?,?,?)'
    for chunk in _staged_condition_chunks(a,10000):
        n=len(chunk)
        ds_a_h=mapped_header(a.mapping,'death_status');cs_a_h=mapped_header(a.mapping,'case_status')
        dod_a_h=mapped_header(a.mapping,'date_of_death')
        dsa=string_col(chunk,ds_a_h).tolist() if ds_a_h else ['']*n
        csa=string_col(chunk,cs_a_h).tolist() if cs_a_h else ['']*n
        doda=string_col(chunk,dod_a_h).tolist() if dod_a_h else ['']*n
        b_ref={}
        refs=sorted({int(x) for x in matched[offset:offset+n] if x>=0})
        if refs:
            for part in [refs[i:i+850] for i in range(0,len(refs),850)]:
                q=','.join('?'*len(part))
                b_ref.update({int(k):(death,case,dod,note,json.loads(payload)) for k,death,case,dod,note,payload in conn.execute(f'SELECT row_id,death_status,case_status,dod,notes,payload FROM b_selected WHERE row_id IN ({q})',part)})
        # Align matched File B values to each File A row for cross-file
        # conditions, without loading the entire reference list into RAM.
        cross_rules=[r for r in config.get('rules',[]) if r.get('enabled') and r.get('source')=='cross'] if config.get('enabled',True) else []
        aligned_reference=None
        if cross_rules:
            fields=sorted({r['compare_header'] for r in cross_rules})
            indexes={h:b.headers.index(h) for h in fields}
            aligned_reference=pd.DataFrame({h:[
                (b_ref.get(int(mid),('','','','',[]))[4] or [])[indexes[h]]
                if int(mid)>=0 and int(mid) in b_ref else ''
                for mid in matched[offset:offset+n]] for h in fields},index=chunk.index)
        flags=evaluate_frame(chunk,a.mapping,config,'a',
            min_age=options.min_age,max_age=options.max_age,
            aligned_reference=aligned_reference,matched_mask=(np.asarray(matched[offset:offset+n])>=0) &
                np.isin(np.asarray(types[offset:offset+n]),
                    [eng.TYPE_TRUE,eng.TYPE_FUZZY,eng.TYPE_TRUE_SWAP,eng.TYPE_FUZZY_SWAP]))
        write=[]
        for i in range(n):
            notes=flags.get(i,[]).copy()
            brow=int(matched[offset+i]);dbs,bcs,bdod,bnotes,bvalues=b_ref.get(brow,('','','','',[]))
            accepted_duplicate=int(types[offset+i]) in (eng.TYPE_TRUE,eng.TYPE_FUZZY,eng.TYPE_TRUE_SWAP,eng.TYPE_FUZZY_SWAP)
            if config.get('enabled',True) and b is not None and brow>=0:
                if config['builtins'].get('birthdate_conflict'):
                    ab=mapped_header(a.mapping,'birthdate');bb=mapped_header(b.mapping,'birthdate')
                    if ab and bb:
                        ad=parse_date(chunk.iloc[i][ab]);bd=parse_date(bvalues[b.headers.index(bb)]) if bvalues else None
                        if ad and bd and ad!=bd: notes.append('Birthdate conflict with File B')
                if config['builtins'].get('cross_status_conflict'):
                    if ds_a_h and mapped_header(b.mapping,'death_status') and basic(dsa[i]) and basic(dbs) and basic(dsa[i])!=basic(dbs):
                        notes.append('Death status conflicts with File B')
                    if cs_a_h and mapped_header(b.mapping,'case_status') and basic(csa[i]) and basic(bcs) and basic(csa[i])!=basic(bcs):
                        notes.append('Case status conflicts with File B')
                if bnotes:
                    # A validated/note-only File B rule is reference information; it
                    # must not become an independent REVIEW condition for File A.
                    bparts=[x.strip() for x in bnotes.split('; ') if x.strip()]
                    for part in bparts:
                        is_reference_only=part.startswith(('NOTE: ','VALIDATED RECORDS: ')) or (accepted_duplicate and part.startswith('DUPLICATE RECORDS: '))
                        notes.append(('NOTE: File B: ' if is_reference_only else 'File B: ')+part)
            notes=list(dict.fromkeys(notes))
            rid=offset+i
            unconfirmed=any(x.startswith('DUPLICATE RECORDS: ') for x in notes) and not accepted_duplicate
            if unconfirmed:
                notes.append('UNCONFIRMED DUPLICATE REQUEST: no accepted identity match; manual review required')
            def review_action(note):
                if note.startswith(('NOTE: ','VALIDATED RECORDS: ')): return False
                if note.startswith('DUPLICATE RECORDS: '): return not accepted_duplicate
                return True
            promote=any(review_action(x) for x in notes)
            if promote or notes or dsa[i] or dbs:
                write.append((rid,'; '.join(notes),dsa[i],dbs,int(promote)))
                if promote:
                    triggered+=1
                    if int(types[rid])==eng.TYPE_CLEAN:
                        types[rid]=eng.TYPE_FINDING;promoted+=1
        if write:conn.executemany(sql,write);conn.commit()
        offset+=n
    types.flush()
    if offset!=a.rows: raise RuntimeError(f'Condition row alignment mismatch: {offset}/{a.rows}.')
    log(f'Condition evaluation complete: {triggered:,} flagged File A record(s); {promoted:,} CLEAN records moved to REVIEW. Duplicate matches retained.')
    conn.close()
    return {'db':str(db),'flagged':triggered,'promoted':promoted,'b_selected':len(matches)}
