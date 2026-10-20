"""Conservative header inference. Never infer a sensitive meaning from generic Status alone."""
from __future__ import annotations
import re
import unicodedata
from collections import Counter
from rapidfuzz import fuzz

SEMANTICS = {
 'full_name': ('Full name','full name','fullname','complete name','beneficiary name','client name','name of beneficiary','complete beneficiary name','name of client'),
 'first': ('First name','first name','firstname','fname','given name','given_name','first_nme','beneficiary first name','client first name','name first','given_name_1'),
 'middle': ('Middle name','middle name','middlename','mname','middle initial','mi','beneficiary middle name','client middle name','mid name'),
 'last': ('Last name','last name','lastname','surname','family name','lastnme','beneficiary last name','client last name','beneficiary surname'),
 'suffix': ('Suffix','suffix','ext name','extended name','name extension','extension','name ext','ext','suffix name'),
 'birthdate': ('Birthdate','dob','birth date','birthdate','date of birth','birthday','birth dt','birth_date','bdate','birthdate of beneficiary'),
 'birth_month': ('Birth month','birth month','month of birth','dob month','birth mm'),
 'birth_day': ('Birth day','birth day','day of birth','dob day','birth dd'),
 'birth_year': ('Birth year','birth year','year of birth','dob year','birth yyyy'),
 'death_status': ('Death status','death status','deceased status','life status','mortality status','deceased indicator','death indicator','is deceased'),
 'date_of_death': ('Date of death','date of death','death date','dod','date died'),
 'case_status': ('Beneficiary status','beneficiary status','verification status','record status','case status'),
 'payment_status': ('Payment status','payment status','payout status','disbursement status','paid status'),
 'date_accomplished': ('Date accomplished','date accomplished','date of accomplished','accomplishment date','date interviewed','date accomplished on','accomplished date','interview date'),
 'person_id': ('Person identifier','person id','person_id','beneficiary id','beneficiary_id','hhid','household id','client id','beneficiary number','record id','hh id'),
 'paid_at': ('Paid at','paid at','paid date','date paid'),
 'payout_site': ('Payout site','payout site','payment venue','payout location'),
 'province': ('Province','province','prov'),
 'municipality': ('Municipality','municipality','city municipality','city mun name','lgu','city','city municipal','mun city','city mun'),
 'barangay': ('Barangay','barangay','brgy','bgy','brgy name'),
}
CORE = {'full_name','first','middle','last','suffix','birthdate','birth_month','birth_day','birth_year'}
CRITICAL = CORE | {'death_status','date_of_death','person_id','case_status','payment_status'}
def normalize(v):
    s=unicodedata.normalize('NFKD',str(v or '')).casefold()
    return ' '.join(re.sub(r'[^a-z0-9]+',' ',s).split())
def compact(v): return normalize(v).replace(' ','')
CANON = {k:[normalize(x) for x in v[1:]] for k,v in SEMANTICS.items()}

# Explicit DOB component labels beat fuzzy string scores. A suffix such as MM,
# DD, or YYYY describes the FORMAT of a split component, not a full date.
def explicit_birth_semantic(header):
    words = normalize(header).split()
    s = ''.join(words)
    components = {
        'birthmonth': 'birth_month', 'monthofbirth': 'birth_month', 'dobmonth': 'birth_month',
        'dayofbirth': 'birth_day', 'dobday': 'birth_day',
        'birthyear': 'birth_year', 'yearofbirth': 'birth_year', 'dobyear': 'birth_year',
    }
    if words[:2] == ['birth', 'day'] and (len(words) == 2 or (len(words) == 3 and words[2] in {'dd', 'd'})):
        return 'birth_day'
    for stem, role in components.items():
        if s == stem:
            return role
        tail = s[len(stem):]
        if s.startswith(stem) and tail in ({'mm', 'm'} if role == 'birth_month' else
                                         {'dd', 'd'} if role == 'birth_day' else {'yyyy', 'yy', 'y'}):
            return role
    return ''


def birth_value_shape(value):
    """Classify values for semantic validation, not DOB parsing or matching."""
    value = str(value).strip()
    if not value or value.casefold() in {'nan', 'nat', 'none', 'null', '<na>', 'n/a'}:
        return ''
    # Full date/datetime, including Excel/Pandas ISO datetime strings.
    if re.match(r'^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s|$|T)', value, re.I):
        return 'full'
    if re.match(r'^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}(?:\s|$)', value):
        return 'full'
    if re.fullmatch(r'\d{8}', value):
        return 'full'
    if re.fullmatch(r'\d{1,5}(?:\.0+)?', value):
        n = int(float(value))
        if 1 <= n <= 31:
            return 'day'
        if 1850 <= n <= 2100:
            return 'year'
        if 10000 <= n <= 85000:
            return 'full'  # Excel date serial, not a day component
        return 'unknown'
    months={'january','february','march','april','may','june','july','august','september','october','november','december',
            'jan','feb','mar','apr','jun','jul','aug','sep','sept','oct','nov','dec'}
    if value.casefold() in months:
        return 'month'
    return 'unknown'


def validated_birth_mapping(header, role, samples, has_split_context, has_day_header):
    """Return role, confidence-override, warning. Never silently infer DOB from day."""
    observed = [str(v).strip() for v in samples if str(v).strip().casefold() not in
                ('', 'nan', 'nat', 'none', 'null', '<na>', 'n/a')][:40]
    shapes = [birth_value_shape(v) for v in observed]
    count = len(shapes)
    if not count:
        if normalize(header) == 'birth date' and has_split_context and not has_day_header:
            return role, 0, 'Birth Date could be a full date or a day component; confirm from values.'
        if compact(header) == 'birthdate':
            return role, None, ''  # Literal Birthdate is a full date unless samples contradict it.
        return role, None, ''
    n = Counter(shapes)
    dominant, num = n.most_common(1)[0]
    # Only infer a shape when enough nonempty evidence agrees.
    if num / count < 0.8:
        if role in {'birthdate','birth_day','birth_month','birth_year'}:
            return role, 0, 'Mixed or invalid date samples; select the correct DOB meaning manually.'
        return role, None, ''
    if dominant == 'full':
        if role != 'birthdate':
            return 'birthdate', 0, 'Full dates found in a field labeled as a date component; confirm Full Birthdate.'
        return role, None, ''
    if dominant == 'year':
        if role != 'birth_year':
            return 'birth_year', 0, 'Year-only values found; confirm Birth Year, not Full Birthdate.'
        return role, None, ''
    if dominant == 'day':
        if role == 'birthdate':
            if normalize(header) == 'birth date' and has_split_context and not has_day_header:
                return 'birth_day', 99, ''  # Sample-backed government split-date usage.
            return 'birth_day', 0, 'Only day numbers found; confirm Birth Day instead of Full Birthdate.'
        # 1–12 is ambiguous between day and month. Explicit header resolves it.
        return role, None, ''
    if dominant == 'month' and role != 'birth_month':
        return 'birth_month', 0, 'Month names found; confirm Birth Month.'
    return role, None, ''

def detect_headers(headers, samples=None, high=95, suggest=85):
    samples=samples or {}
    all_normalized={compact(h) for h in headers}
    explicit_roles={h:explicit_birth_semantic(h) for h in headers}
    split_dob=any(v=='birth_month' for v in explicit_roles.values()) and any(v=='birth_year' for v in explicit_roles.values())
    has_day_header=any(v=='birth_day' for v in explicit_roles.values())
    results=[]
    for header in headers:
        h=normalize(header); hc=compact(header)
        # Generic Status is a dangerous alias across unrelated programs.
        if hc=='status':
            vals=[normalize(v) for v in samples.get(header,[]) if normalize(v)]
            deaths={'alive','deceased','dead','died','not deceased','living'}
            payments={'paid','unpaid','pending','released','for payout','claimed'}
            hint='death_status' if vals and all(v in deaths for v in vals) else 'payment_status' if vals and all(v in payments for v in vals) else ''
            results.append(dict(header=header,key='',suggestion=hint,confidence=0,band='ambiguous',method='generic Status',confirmed=False,warning='Generic Status is ambiguous; select meaning manually.'))
            continue
        scores=[]
        for key,labels in CANON.items():
            for i,label in enumerate(labels):
                if h==label:
                    # the first alias is the proper label; following are known aliases
                    score = 100 if i==0 else 98
                    method = 'exact' if i==0 else 'alias'
                elif hc==label.replace(' ',''):
                    score=97;method='normalized'
                else:
                    a=set(h.split());z=set(label.split())
                    tok=85*(2*len(a&z)/max(len(a)+len(z),1)) if a and z else 0
                    fuzzy=fuzz.ratio(hc,label.replace(' ',''))
                    # Word order variations are common in generated LGU sheets.
                    # Avoid partial-match scoring: a short generic fragment such
                    # as "status" must NOT be silently treated as death status.
                    words=fuzz.token_sort_ratio(h,label) if len(h)>=6 and len(label)>=6 else 0
                    score=round(min(96,max(tok,fuzzy,words)),1);method='token/fuzzy'
                scores.append((score,key,method))
        scores.sort(reverse=True)
        top=scores[0]; runner=next((s for s in scores[1:] if s[1]!=top[1]),(0,'',''))
        explicit_role=explicit_roles[header]
        if explicit_role:
            top=(100,explicit_role,'explicit DOB component')
            runner=(0,'','')
        elif hc in {'birthdate','dateofbirth','dob','birthday','bdate','birthdt'}:
            top=(100,'birthdate','explicit full DOB header')
            runner=(0,'','')
        date_warning=''
        date_confidence=None
        if top[1] in {'birthdate','birth_day','birth_month','birth_year'}:
            corrected,date_confidence,date_warning=validated_birth_mapping(
                header,top[1],samples.get(header,[]),split_dob,has_day_header)
            if corrected!=top[1]:
                top=(99 if date_confidence else 88,corrected,'sample value shape')
                runner=(0,'','')
        ambiguous = (top[0]-runner[0]<7 and runner[0]>=suggest)
        confident=top[0]>=high and not ambiguous and (top[2]!='token/fuzzy' or top[1] not in CRITICAL)
        suggested=top[0]>=suggest
        if date_confidence == 0:
            confident=False; ambiguous=True; suggested=True
        elif date_confidence is not None:
            confident=bool(date_confidence>=high)
        key=top[1] if confident else ''
        # Sample-value plausibility validation: a correctly spelled sensitive
        # header can still contain data belonging to a different field.
        observed=[normalize(v) for v in samples.get(header,[]) if normalize(v)][:40]
        sample_conflict=False
        if observed and suggested and top[1]=='death_status':
            death_values={'alive','living','deceased','dead','died','not deceased','unknown','for verification','unlocated','waived'}
            if sum(v not in death_values for v in observed)>len(observed)*0.50:sample_conflict=True
        if observed and suggested and top[1]=='payment_status':
            if sum(v in {'alive','living','deceased','dead','died','not deceased'} for v in observed)>len(observed)*0.50:sample_conflict=True
        if observed and suggested and top[1]=='birth_year':
            if sum(not (v.isdigit() and 1850<=int(v)<=2100) for v in observed)>len(observed)*0.50:sample_conflict=True
        if observed and suggested and top[1] in ('birth_day','birth_month'):
            monthwords={'january','february','march','april','may','june','july','august','september','october','november','december','jan','feb','mar','apr','jun','jul','aug','sep','sept','oct','nov','dec'}
            bound=31 if top[1]=='birth_day' else 12
            if sum(not ((v.isdigit() and 1<=int(v)<=bound) or (top[1]=='birth_month' and v in monthwords)) for v in observed)>len(observed)*0.50:sample_conflict=True
        if sample_conflict:
            confident=False;ambiguous=True;key='';top=(min(top[0],84),top[1],top[2])
            suggested=True  # retain the possible meaning, but force explicit confirmation
        if date_warning:
            confident=False;ambiguous=True;key='';suggested=True
        warning=(date_warning if date_warning else
                 'Header/sample-data conflict or competing mappings; manually confirm.' if ambiguous else
                 'Critical field requires manual confirmation.' if suggested and not confident and top[1] in CRITICAL else
                 'Suggested mapping; verify before using.' if suggested and not confident else 'Unknown; map manually.')
        results.append(dict(header=header,key=key,suggestion=top[1] if suggested else '',confidence=top[0] if suggested else 0,
                            band='high' if confident else 'suggested' if suggested and not ambiguous else 'conflict',
                            method=top[2],confirmed=confident,warning='' if confident else warning,
                            sample_values=[str(v)[:35] for v in samples.get(header,[]) if birth_value_shape(v) and top[1] in
                                           {'birthdate','birth_month','birth_day','birth_year'}][:2]))
    # Distinct physical columns cannot both be silently assigned to a unique semantic role.
    counts=Counter(r['key'] for r in results if r['key'] and r['key'] not in ('person_id','paid_at'))
    for r in results:
        if r['key'] and counts[r['key']]>1:
            r.update(key='',band='conflict',confirmed=False,warning='Duplicate meaning: choose the correct column manually.')
    return results
