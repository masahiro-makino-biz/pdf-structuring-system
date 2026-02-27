#!/bin/bash
echo "=== 1. LiteLLM verbose有効化 ==="
docker exec pdf-litellm sed -i 's/set_verbose: false/set_verbose: true/' /app/config.yaml
docker restart pdf-litellm
echo "10秒待機..."
sleep 10

echo ""
echo "=== 2. rawリクエスト(成功するはず) ==="
docker exec pdf-api python -c "import requests; r = requests.post('http://litellm:4000/v1/chat/completions', headers={'Authorization': 'Bearer sk-litellm', 'Content-Type': 'application/json'}, json={'model':'azure-gpt-4o','messages':[{'role':'user','content':'hi'}]}); print(f'status={r.status_code}')"

echo ""
echo "=== 3. OpenAI SDK(失敗するはず) ==="
docker exec pdf-api python -c "from openai import OpenAI; c = OpenAI(api_key='sk-litellm', base_url='http://litellm:4000/v1'); r = c.chat.completions.create(model='azure-gpt-4o', messages=[{'role':'user','content':'hi'}]); print('OK')" 2>&1 || true

echo ""
echo "=== 4. LiteLLMログ(直近30行) ==="
docker logs pdf-litellm --tail 30

echo ""
echo "=== 5. verbose戻す ==="
docker exec pdf-litellm sed -i 's/set_verbose: true/set_verbose: false/' /app/config.yaml
