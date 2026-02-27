#!/bin/bash
echo "=== 1. LiteLLMに直接リクエスト ==="
docker exec pdf-api python -c "import requests; r = requests.post('http://litellm:4000/v1/chat/completions', headers={'Authorization': 'Bearer sk-litellm', 'Content-Type': 'application/json'}, json={'model':'azure-gpt-4o','messages':[{'role':'user','content':'hi'}]}); print(f'status={r.status_code}'); print(r.text[:300])"

echo ""
echo "=== 2. api側の設定確認 ==="
docker exec pdf-api python -c "from core.config import get_settings; s=get_settings(); print(f'api_key={s.litellm_api_key}'); print(f'url={s.litellm_url}')"

echo ""
echo "=== 3. chat_service.pyの該当行 ==="
docker exec pdf-api grep "api_key" /app/services/chat_service.py

echo ""
echo "=== 4. pdf_processor.pyの該当行 ==="
docker exec pdf-api grep "api_key" /app/services/pdf_processor.py

echo ""
echo "=== 5. LiteLLMのconfig確認 ==="
docker exec pdf-litellm cat /app/config.yaml | grep -A2 general
