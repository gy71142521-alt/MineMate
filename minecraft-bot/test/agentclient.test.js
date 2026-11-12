const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { createAgentClient, validIntent } = require('../src/agentclient');

const intent = (name = 'conversation', args = {}) => ({ intent: name, arguments: args, reply: '你好' });
const response = (data = intent(), status = 200) => new Response(JSON.stringify({ success: status === 200, data, error: status === 200 ? null : { code: 'ERROR', message: 'unit-test-secret' } }), { status });
const logger = { error: () => {} };

test('取消玩家请求会中止 HTTP 且不显示错误回复，客户端仍可接收后续消息', async () => {
  let now = 0;
  const client = createAgentClient({}, { logger, clock: () => now, fetchImpl: async (_url, { signal }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  }) });
  const pending = client.message('Steve', '执行工具');
  client.cancel('Steve');
  assert.equal(await pending, null);
  now = 4000;
  const second = client.message('Steve', '再试一次');
  client.cancel('Steve');
  assert.equal(await second, null);
});

test('Agent 客户端发送玩家与消息，解析结构化结果但不执行意图', async () => {
  const calls = [];
  const client = createAgentClient({}, { logger, fetchImpl: async (url, options) => {
    calls.push([url, JSON.parse(options.body)]);
    return response(intent('goto', { x: 100, y: 64, z: 200 }));
  } });
  const result = await client.message('Steve', '去坐标 100 64 200');
  assert.equal(result.intent, 'goto');
  assert.equal(result.reply, 'MineMate：你好');
  assert.deepEqual(calls, [['http://127.0.0.1:8000/api/agent/message', { player: 'Steve', message: '去坐标 100 64 200' }]]);
});

test('Agent 客户端拒绝未知意图、非法坐标、无效 JSON 和超大响应', async () => {
  const values = [response(intent('run_shell')), response(intent('goto', { x: 1, y: 9999, z: 1 })),
    response(intent('follow', { command: '/kill' })), new Response('invalid JSON'), new Response('x'.repeat(17000))];
  for (const value of values) {
    const errors = [];
    const client = createAgentClient({}, { logger: { error: (text) => errors.push(text) }, fetchImpl: async () => value });
    const result = await client.message('Steve', 'hi');
    assert.match(result.reply, /暂不可用|失败/);
    assert.equal(result.intent, undefined);
    assert.ok(errors.length > 0);
  }
  assert.equal(validIntent(intent('goto', { x: true, y: 64, z: 0 })), false);
});

test('Python 不可用或返回错误时提供固定提示，不回显服务端敏感内容', async () => {
  for (const fetchImpl of [async () => { throw new Error('unit-test-secret'); }, async () => response(null, 503), async () => response(null, 429)]) {
    const errors = [];
    const client = createAgentClient({}, { logger: { error: (text) => errors.push(text) }, fetchImpl });
    const result = await client.message('Steve', 'hi');
    assert.match(result.reply, /失败|暂不可用|繁忙/);
    assert.doesNotMatch(JSON.stringify({ result, errors }), /unit-test-secret/);
  }
});

test('超时会中止请求，断线清理会中止在途请求且不发送错误回复', async () => {
  const fetchImpl = (_url, { signal }) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }));
  const timed = createAgentClient({ timeout: 10 }, { logger, fetchImpl });
  assert.match((await timed.message('Steve', 'hi')).reply, /超时/);
  const client = createAgentClient({}, { logger, fetchImpl });
  const pending = client.message('Steve', 'hi');
  client.dispose();
  assert.equal(await pending, null);
  assert.equal(await client.message('Alex', 'hi'), null);
});

test('并发、全局频率、玩家冷却均有界，旧请求失败后释放并发槽', async () => {
  let now = 0;
  const releases = [];
  let requests = 0;
  const client = createAgentClient({ maxConcurrent: 1, requestsPerMinute: 2, playerCooldown: 3000 }, {
    logger, clock: () => now, fetchImpl: () => { requests += 1; return new Promise((resolve) => releases.push(() => resolve(response()))); }
  });
  const first = client.message('Steve', 'hi');
  assert.equal(await client.message('Alex', 'hi'), null);
  releases.shift()(); await first;
  assert.equal(await client.message('sTeVe', 'hi'), null);
  now = 3001;
  const second = client.message('Steve', 'hi'); releases.shift()(); await second;
  now = 6000;
  assert.equal(await client.message('Alex', 'hi'), null);
  now = 60001;
  const third = client.message('Alex', 'hi'); releases.shift()(); await third;
  assert.equal(requests, 3);
});

test('输入过长不请求模型，回复有固定前缀、脱敏和聊天长度限制', async () => {
  let calls = 0;
  const client = createAgentClient({}, { logger, fetchImpl: async () => {
    calls += 1;
    return response({ ...intent(), reply: '/kill @e\n sk-unit-secret ' + '字'.repeat(400) });
  } });
  assert.match((await client.message('Steve', 'x'.repeat(501))).reply, /长度/);
  assert.equal(calls, 0);
  const result = await client.message('Steve', 'hi');
  assert.ok(result.reply.startsWith('MineMate：/kill'));
  assert.ok(result.reply.length <= 240);
  assert.doesNotMatch(result.reply, /sk-unit-secret|\n/);
});

test('实际回环 HTTP 传输验证 Node 到 Agent 的路径、JSON 和返回结果', async (t) => {
  const calls = [];
  const server = http.createServer(async (request, res) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    calls.push([request.url, JSON.parse(Buffer.concat(chunks))]);
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ success: true, data: intent('stop'), error: null }));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const client = createAgentClient({ url: `http://127.0.0.1:${server.address().port}` }, { logger });
  const result = await client.message('Steve', '停下来');
  assert.equal(result.intent, 'stop');
  assert.deepEqual(calls, [['/api/agent/message', { player: 'Steve', message: '停下来' }]]);
});

test('真实 HTTP 响应体迟迟不结束时仍受总超时限制', async (t) => {
  const server = http.createServer((_request, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.write('{"success":');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const client = createAgentClient({ url: `http://127.0.0.1:${server.address().port}`, timeout: 100 }, { logger });
  const result = await client.message('Steve', 'hi');
  assert.match(result.reply, /超时/);
});
