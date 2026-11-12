const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createApiServer } = require('../src/httpServer');

async function startFixture({ ready = true } = {}) {
  const bot = new EventEmitter();
  bot.entity = ready ? { position: { x: 0, y: 64, z: 0 } } : undefined;
  const calls = [];
  const movement = {
    follow: (username) => { calls.push(['follow', username]); return { ok: true, status: 'follow', message: '正在跟随', username, taskId: 1 }; },
    goTo: (username, x, y, z) => { calls.push(['goto', username, x, y, z]); return { ok: true, status: 'goto', message: '正在前往', username, taskId: 2 }; },
    stop: (username) => { calls.push(['stop', username]); return { ok: true, status: 'stopped', message: '已停止移动', username, taskId: null }; }
  };
  const perception = { getWorldSnapshot: () => ({ timestamp: 1, availability: { player: ready ? 'available' : 'unavailable' }, player: {}, inventory: [], equipment: [], nearbyEntities: [], nearbyBlocks: [], truncated: { entities: false, blocks: false }, limits: {} }) };
  const server = createApiServer({ bot, movement, perception, getStatus: () => ({ connected: ready, spawned: ready, state: ready ? 'connected' : 'connecting' }), maxBodyBytes: 64 });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  async function request(path, options = {}) {
    const response = await fetch(`http://127.0.0.1:${port}${path}`, options);
    return { status: response.status, body: await response.json() };
  }
  return { server, calls, request };
}

test('HTTP 健康、世界和三个移动接口返回统一响应并复用现有模块', async (t) => {
  const fixture = await startFixture();
  t.after(() => fixture.server.close());
  let result = await fixture.request('/health');
  assert.equal(result.status, 200);
  assert.deepEqual(Object.keys(result.body), ['success', 'data', 'error']);
  assert.equal(result.body.data.bot.ready, true);
  result = await fixture.request('/api/world');
  assert.equal(result.status, 200);
  assert.equal(result.body.success, true);
  const json = (value) => ({ method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(value) });
  assert.equal((await fixture.request('/api/movement/follow', json({ username: 'Steve' }))).body.success, true);
  assert.equal((await fixture.request('/api/movement/goto', json({ username: 'Steve', x: 1, y: 64, z: -2 }))).body.success, true);
  assert.equal((await fixture.request('/api/movement/stop', json({ username: 'Steve' }))).body.success, true);
  assert.deepEqual(fixture.calls, [['follow', 'Steve'], ['goto', 'Steve', 1, 64, -2], ['stop', 'Steve']]);
});

test('HTTP 接口拒绝非法参数、过大请求体和未知路径', async (t) => {
  const fixture = await startFixture();
  t.after(() => fixture.server.close());
  const json = (value) => ({ method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(value) });
  let result = await fixture.request('/api/movement/follow', json({ username: 'bad name' }));
  assert.equal(result.status, 400);
  assert.equal(result.body.error.code, 'INVALID_ARGUMENT');
  result = await fixture.request('/api/movement/goto', json({ username: 'Steve', x: '1', y: 2, z: 3 }));
  assert.equal(result.status, 400);
  result = await fixture.request('/api/movement/stop', { method: 'POST', body: JSON.stringify({ username: 'Steve' }) });
  assert.equal(result.status, 415);
  result = await fixture.request('/api/movement/stop', { method: 'POST', headers: { 'content-type': 'application/json' }, body: 'x'.repeat(100) });
  assert.equal(result.status, 413);
  result = await fixture.request('/missing');
  assert.equal(result.status, 404);
  assert.equal(result.body.success, false);
});

test('Bot 未连接时健康接口仍可用，但世界和移动接口明确返回不可用', async (t) => {
  const fixture = await startFixture({ ready: false });
  t.after(() => fixture.server.close());
  assert.equal((await fixture.request('/health')).body.data.bot.ready, false);
  assert.equal((await fixture.request('/api/world')).status, 503);
  const result = await fixture.request('/api/movement/stop', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'Steve' }) });
  assert.equal(result.status, 503);
  assert.equal(result.body.error.code, 'BOT_NOT_READY');
  assert.deepEqual(fixture.calls, []);
});

test('移动模块拒绝任务或抛出异常时不会泄漏内部堆栈', async (t) => {
  const fixture = await startFixture();
  fixture.server.close();
  const bot = new EventEmitter();
  bot.entity = { position: { x: 0, y: 0, z: 0 } };
  const server = createApiServer({ bot, movement: { follow: () => { throw new Error('secret stack'); } }, perception: null, getStatus: () => ({ connected: true, spawned: true }) });
  t.after(() => server.close());
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const response = await fetch(`http://127.0.0.1:${server.address().port}/api/movement/follow`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'Steve' }) });
  const body = await response.json();
  assert.equal(response.status, 500);
  assert.equal(body.error.code, 'MOVEMENT_FAILED');
  assert.doesNotMatch(JSON.stringify(body), /secret stack/);
});
