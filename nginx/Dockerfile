FROM nginx:alpine

COPY default.conf.template /etc/nginx/templates/default.conf.template

# Genera nginx config con la API key inyectada en tiempo de inicio
CMD ["/bin/sh", "-c", \
  "envsubst '${API_KEY}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
