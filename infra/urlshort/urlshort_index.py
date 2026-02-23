import os, json, time, random, string, boto3, base64

ddb = boto3.resource('dynamodb')
TABLE_NAME = os.getenv('TABLE_NAME', 'jrk-url-short')
BASE_DOMAIN = os.getenv('BASE_DOMAIN', '')
_CHARS = string.ascii_letters + string.digits

def _table():
    return ddb.Table(TABLE_NAME)

def _gen_code(n=7):
    return ''.join(random.choice(_CHARS) for _ in range(n))

def handler(event, context):
    path = event.get('rawPath') or event.get('path', '/')
    method = (event.get('requestContext', {}).get('http', {}).get('method') or event.get('httpMethod') or 'GET').upper()

    if method == 'POST' and path.endswith('/shorten'):
        try:
            body = event.get('body') or '{}'
            if event.get('isBase64Encoded'):
                body = base64.b64decode(body).decode('utf-8', 'ignore')
            payload = json.loads(body)
            url = (payload.get('url') or '').strip()
            ttl = int(payload.get('ttl_seconds') or 7*24*3600)
            if not url:
                return {'statusCode': 400, 'body': 'missing url'}
            code = _gen_code()
            expire = int(time.time()) + ttl
            _table().put_item(Item={'code': code, 'url': url, 'expireAt': expire})
            dom = event.get('requestContext', {}).get('domainName') or BASE_DOMAIN.strip()
            scheme = 'https'
            short_url = f"{scheme}://{dom}/{code}" if dom else f"/{code}"
            return {'statusCode': 200, 'headers': {'Content-Type': 'application/json'}, 'body': json.dumps({'code': code, 'short_url': short_url})}
        except Exception as e:
            return {'statusCode': 500, 'body': str(e)}

    if method == 'GET':
        code = path.strip('/').split('/')[-1]
        if not code:
            return {'statusCode': 400, 'body': 'missing code'}
        try:
            resp = _table().get_item(Key={'code': code})
            item = resp.get('Item')
            if not item:
                return {'statusCode': 404, 'body': 'not found'}
            return {'statusCode': 302, 'headers': {'Location': item['url']}, 'body': ''}
        except Exception as e:
            return {'statusCode': 500, 'body': str(e)}
