#!/bin/bash
echo "=== 1. rawリクエスト(requests)でLiteLLM ==="
docker exec pdf-api python -c "import requests; r = requests.post('http://litellm:4000/v1/chat/completions', headers={'Authorization': 'Bearer sk-litellm', 'Content-Type': 'application/json'}, json={'model':'azure-gpt-4o','messages':[{'role':'user','content':'hi'}]}); print(f'status={r.status_code}')"

echo ""
echo "=== 2. OpenAI SDK(sync)でLiteLLM ==="
docker exec pdf-api python -c "from openai import OpenAI; c = OpenAI(api_key='sk-litellm', base_url='http://litellm:4000/v1'); r = c.chat.completions.create(model='azure-gpt-4o', messages=[{'role':'user','content':'hi'}]); print('OK:', r.choices[0].message.content[:50])"

echo ""
echo "=== 3. OpenAI SDK(async)でLiteLLM ==="
docker exec pdf-api python -c "import asyncio; from openai import AsyncOpenAI; c = AsyncOpenAI(api_key='sk-litellm', base_url='http://litellm:4000/v1'); r = asyncio.run(c.chat.completions.create(model='azure-gpt-4o', messages=[{'role':'user','content':'hi'}])); print('OK:', r.choices[0].message.content[:50])"

echo ""
echo "=== 4. Agents SDK(Runner)でLiteLLM ==="
docker exec pdf-api python -c "import asyncio; from openai import AsyncOpenAI; from agents import Agent, Runner, set_default_openai_client; c = AsyncOpenAI(api_key='sk-litellm', base_url='http://litellm:4000/v1'); set_default_openai_client(c); agent = Agent(name='test', instructions='say hi', model='azure-gpt-4o'); r = asyncio.run(Runner.run(agent, 'hello')); print('OK:', r.final_output[:50])"
