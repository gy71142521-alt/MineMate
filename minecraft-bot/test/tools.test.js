const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { Vec3 } = require('vec3');
const { createRegistry } = require('../src/tools/registry');
const { createToolSystem } = require('../src/tools');
const { object, number } = require('../src/tools/schema');
function fixture(options = {}) {
  const bot = new EventEmitter();
  Object.assign(bot, { entity: { position: new Vec3(0, 64, 0) }, isAlive: true });
  const movement = { stop: () => ({ ok: true }), follow: () => ({ ok: true }), goTo: () => ({ ok: true }) };
  const logger = { log() {}, error() {} };
  const registry = createRegistry(bot, { movement, logger, ...options });
  bot.emit('spawn');
  return { bot, movement, registry };
}
test('旧移动与感知路径仅转发相同的实现', () => {
  assert.equal(require('../src/movement'), require('../src/tools/movement'));
  for (const name of ['index', 'playerState', 'inventory', 'nearbyEntities', 'nearbyBlocks']) {
    assert.equal(require(`../src/perception/${name}`), require(`../src/tools/perception/${name}`));
  }
});
test('注册目录拒绝重名、未知工具及非法参数，返回定义副本', async () => {
  const { registry } = fixture();
  let called = 0;
  const definition = { name: 'demo', description: 'test', parameters: object({ x: number(0, 4, true) }), execute: () => { called++; return { status: 'completed' }; } };
  registry.register(definition);
  assert.throws(() => registry.register(definition));
  registry.list()[0].parameters.required.push('fake');
  for (const [name, args] of [['unknown', {}], ['demo', { x: true }], ['demo', { x: 5 }], ['demo', { x: 1, code: 'evil' }]]) {
    assert.equal((await registry.execute(name, args, 'Steve')).success, false);
  }
  assert.equal(called, 0);
  assert.equal((await registry.execute('demo', { x: 2 }, 'Steve')).success, true);
  assert.equal(called, 1);
});
test('超时、取消及断线后不会继续后续步骤，锁等待底层 API 结算', async () => {
  for (const event of ['timeout', 'cancel', 'end', 'death']) {
    const { bot, registry } = fixture({ timeoutMs: 10 });
    let release, writes = 0;
    registry.register({ name: 'slow', parameters: object(), execute: async (ctx) => {
      await ctx.step(() => new Promise((resolve) => { release = resolve; }));
      writes++; return {};
    } });
    const pending = registry.execute('slow', {}, 'Steve');
    if (event === 'cancel') registry.cancel('Steve');
    else if (event !== 'timeout') bot.emit(event);
    assert.equal((await pending).success, false);
    assert.equal(registry.getState().active, 'slow');
    release(); await new Promise(setImmediate);
    assert.equal(writes, 0);
    assert.equal(registry.getState().active, null);
  }
});
test('单一修改操作、旧移动互斥、最高优先级停止及主人权限', async () => {
  const { registry, movement } = fixture({ ownerUsername: 'Steve' });
  registry.guardMovement();
  let release;
  registry.register({ name: 'slow', parameters: object(), execute: (ctx) => ctx.step(() => new Promise((resolve) => { release = resolve; })) });
  assert.equal((await registry.execute('slow', {}, 'Alex')).error.code, 'FORBIDDEN');
  const pending = registry.execute('slow', {}, 'Steve');
  assert.equal(movement.follow('Steve').status, 'busy');
  assert.equal((await registry.execute('slow', {}, 'Steve')).error.code, 'BUSY');
  movement.stop('Steve');
  assert.equal((await pending).error.code, 'CANCELLED');
  release();
});
test('请求 ID 防止重复执行，跟随与导航反馈 pending', async () => {
  const { bot, movement } = fixture();
  let count = 0;
  const registry = createToolSystem(bot, { movement, logger: { log() {}, error() {} }, modules: [(r) => r.register({ name: 'once', parameters: object(), execute: () => ({ n: ++count }) })] });
  bot.emit('spawn');
  const [a, b] = await Promise.all([registry.execute('once', {}, 'Steve', 'id1'), registry.execute('once', {}, 'Steve', 'id1')]);
  assert.deepEqual(a, b); assert.equal(count, 1);
  assert.equal((await registry.execute('follow_player', {}, 'Steve')).data.status, 'pending');
  assert.equal((await registry.execute('goto_position', { x: 1, y: 64, z: 0 }, 'Steve')).data.status, 'pending');
  registry.dispose();
});

test('旧请求的延迟取消不能取消另一项新操作', async () => {
  const { registry } = fixture();
  let release;
  registry.register({ name: 'slow', parameters: object(), execute: (ctx) => ctx.step(() => new Promise((resolve) => { release = resolve; })) });
  const pending = registry.execute('slow', {}, 'Steve', 'new-request');
  const before = registry.getState().generation;
  registry.cancel('Steve', false, 'old-request');
  assert.equal(registry.getState().generation, before);
  assert.equal(registry.getState().active, 'slow');
  release({ status: 'completed' });
  assert.equal((await pending).success, true);
});
module.exports = { fixture };
