#!/bin/bash
echo "=== SDK テスト ==="
docker exec pdf-api python -c "from openai import OpenAI; c = OpenAI(api_key='sk-litellm', base_url='http://litellm:4000/v1'); r = c.chat.completions.create(model='azure-gpt-4o', messages=[{'role':'user','content':'hi'}]); print('OK:', r.choices[0].message.content[:50])" 2>&1

echo ""
echo "=== LiteLLMログ(直近5行) ==="
docker logs pdf-litellm --tail 5
