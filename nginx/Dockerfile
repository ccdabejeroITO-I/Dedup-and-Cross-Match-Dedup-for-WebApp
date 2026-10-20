FROM nginx:stable-alpine
RUN apk add --no-cache openssl
COPY default.conf /etc/nginx/conf.d/default.conf
COPY 40-crossmatch-certificate.sh /docker-entrypoint.d/40-crossmatch-certificate.sh
RUN chmod +x /docker-entrypoint.d/40-crossmatch-certificate.sh
