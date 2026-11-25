const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { Vec3 } = require('vec3');
const { createRegistry } = require('../src/tools/registry');
const { createToolSystem } = require('../src/tools');
const { createApiServer } = require('../src/httpServer');
const { object } = require('../src/tools/schema');
const logger = { log() {}, error() {} };

function setup(t, options = {}) {
  const bot = new EventEmitter();
  bot.entity = { id: 1, position: new Vec3(8.5, 64, 8.5) }; bot.isAlive = true;
  let current = { type: 'idle', taskId: null }, starts = 0;
  const movement = { stop: () => { current = { type: 'idle', taskId: null }; return { ok: true }; },
    goTo: () => { current = { type: 'goto', taskId: ++starts }; return { ok: true, taskId: starts }; },
    getState: () => current, setSprint: () => ({ ok: true }), follow: () => ({ ok: true }), jump: async () => ({ ok: true }) };
  const registry = createRegistry(bot, { movement, logger, ...options });
  let writes = 0;
  registry.register({ name: 'write', parameters: object(), execute: () => ({ status: 'completed', n: ++writes }) });
  registry.register({ name: 'read', mutates: false, parameters: object(), execute: () => ({ status: 'completed', n: writes }) });
  registry.register({ name: 'navigate', movement: true, parameters: object(), execute: ({ movement }) => ({ status: 'pending', movement: movement.goTo('Steve') }) });
  registry.guardMovement(); bot.emit('spawn'); t.after(() => registry.dispose());
  const context = () => { const { instanceId, generation } = registry.getState(); return { instanceId, generation }; };
  const acquire = () => { const c = context(); return { ...c, leaseId: registry.lease('acquire', 'Steve', c).lease.leaseId }; };
  return { bot, movement, registry, context, acquire };
}

test('后台租约覆盖操作间隔，读查询仍可用，只有停止或显式导航可抢占', async (t) => {
  const f = setup(t), c = f.acquire();
  assert.equal((await f.registry.execute('write', {}, 'Steve', 'one', c)).success, true);
  assert.equal((await f.registry.execute('write', {}, 'Steve', 'two')).error.code, 'BUSY');
  assert.equal((await f.registry.execute('read', {}, 'Alex', 'read')).success, true);
  assert.equal(f.movement.setSprint('Steve', true).status, 'busy');
  assert.equal((await f.movement.jump('Steve')).status, 'busy');
  f.movement.goTo('Steve');
  assert.equal(f.registry.getState().lease, null);
  assert.equal(f.movement.getState().type, 'goto');
  assert.equal((await f.registry.execute('write', {}, 'Steve', 'three', c)).error.code, 'STALE_LEASE');
});

test('正常释放后台租约不使后续 Runtime 工具请求 generation 过期', async (t) => {
  const f = setup(t), context = f.acquire();
  const before = f.registry.getState().generation;
  f.registry.lease('release', 'Steve', context);
  assert.equal(f.registry.getState().generation, before);
  assert.equal(f.registry.getState().lease, null);
  const result = await f.registry.execute('write', {}, 'Steve', 'after-release', {
    instanceId: context.instanceId, generation: context.generation,
  });
  assert.equal(result.success, true);
});

test('心跳延长租约，过期和生命周期撤销原导航；旧租约不能释放新任务', async (t) => {
  let time = 1000;
  const f = setup(t, { leaseTtlMs: 100, now: () => time }), c = f.acquire();
  await f.registry.execute('navigate', {}, 'Steve', 'nav', c);
  time += 50; f.registry.lease('heartbeat', 'Steve', c);
  time += 60; assert.ok(f.registry.getState().lease);
  time += 50; assert.equal(f.registry.getState().lease, null);
  assert.equal(f.movement.getState().type, 'idle');
  const next = f.acquire();
  assert.throws(() => f.registry.lease('release', 'Steve', { ...c, generation: next.generation }), /租约/);
  assert.equal(f.registry.getState().lease.leaseId, next.leaseId);
  f.bot.emit('death');
  assert.equal(f.registry.getState().lease, null);
  assert.throws(() => f.registry.lease('acquire', 'Steve', f.context()), /就绪/);
});

test('Node 实例 ID 隔离重启，执行回执避免超时后的重复副作用', async (t) => {
  const f = setup(t, { timeoutMs: 10 }), other = setup(t);
  assert.notEqual(f.context().instanceId, other.context().instanceId);
  assert.equal((await other.registry.execute('write', {}, 'Steve', 'old', f.context())).error.code, 'STALE_INSTANCE');
  let release, n = 0;
  f.registry.register({ name: 'slow', parameters: object(), execute: async (ctx) => { await ctx.step(() => new Promise((r) => { release = r; })); return { n: ++n }; } });
  const c = f.acquire();
  const result = await f.registry.execute('slow', {}, 'Steve', 'slow1', c);
  assert.equal(result.error.code, 'TIMEOUT');
  assert.equal(f.registry.receipt('Steve', 'slow1', c).running, true);
  assert.deepEqual(await f.registry.execute('slow', {}, 'Steve', 'slow1', c), result);
  assert.equal(n, 0);
  release(); await new Promise(setImmediate);
  const receipt = f.registry.receipt('Steve', 'slow1', c);
  assert.equal(receipt.running, false); assert.equal(receipt.result.error.code, 'TIMEOUT');
  receipt.result.error.code = 'fake';
  assert.equal(f.registry.receipt('Steve', 'slow1', c).result.error.code, 'TIMEOUT');
  assert.equal(f.registry.receipt('Steve', 'missing', c), null);
});

test('本机真实 HTTP 验证租约、回执和迟到取消不会停止新租约', async (t) => {
  const f = setup(t); f.bot.mineMateStatus = () => ({ connected: true, spawned: true });
  const server = createApiServer({ bot: f.bot, movement: f.movement, tools: f.registry, logger });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  t.after(() => { server.closeAllConnections(); return new Promise((r) => server.close(r)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const post = (path, data) => fetch(base + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const c = f.context();
  const lease = (await (await post('/api/tools/lease', { ...c, username: 'Steve', action: 'acquire' })).json()).data.lease;
  const payload = { ...c, leaseId: lease.leaseId, username: 'Steve', requestId: 'http1', tool: 'write', arguments: {} };
  assert.equal((await (await post('/api/tools/execute', payload)).json()).data.data.n, 1);
  const receipt = await fetch(`${base}/api/tools/requests/http1?username=Steve&instanceId=${c.instanceId}`);
  assert.equal((await receipt.json()).data.result.data.n, 1);
  f.movement.stop('Steve'); const next = f.acquire();
  assert.equal((await post('/api/tools/cancel', { ...c, leaseId: lease.leaseId, username: 'Steve' })).status, 409);
  assert.equal(f.registry.getState().lease.leaseId, next.leaseId);
  assert.equal((await post('/api/tools/lease', { ...next, username: 'Steve', action: 'unknown' })).status, 400);
});

function querySetup(t) {
  const bot = new EventEmitter();
  bot.registry = require('prismarine-registry')('1.21.4');
  bot.entity = { id: 1, position: new Vec3(8.5, 64, 8.5), eyeHeight: 1.62 };
  bot.entities = { 1: bot.entity }; bot.game = { minY: -64, height: 384, dimension: 'overworld' };
  bot.inventory = { items: () => [{ name: 'oak_planks', type: bot.registry.itemsByName.oak_planks.id, count: 4 }] };
  const changed = () => { throw new Error('read operation must not mutate'); };
  const movement = { stop: changed, goTo: changed };
  const tools = createToolSystem(bot, { movement, logger });
  bot.emit('spawn'); t.after(() => tools.dispose());
  bot.craft = changed; bot.dig = changed; bot.setControlState = changed;
  return { bot, tools, run: (name, args) => tools.execute(name, args, 'Steve') };
}

test('配方查询复用实际 Mineflayer 与当前版本配方，展示工作台、批量和材料缺口且只读', async (t) => {
  const { bot, run } = querySetup(t);
  require('mineflayer/lib/plugins/craft')(bot);
  const inventory = JSON.stringify(bot.inventory.items());
  let data = (await run('inspect_recipe', { item_name: 'crafting_table' })).data;
  assert.equal(data.recipes[0].requiresTable, false);
  assert.equal(data.recipes[0].ingredients[0].missing, 0);
  data = (await run('inspect_recipe', { item_name: 'stone_pickaxe', count: 2, max_results: 16 })).data;
  assert.equal(data.recipes[0].requiresTable, true);
  assert.equal(data.recipes[0].output.count, 2);
  assert.ok(data.recipes.some((recipe) => recipe.ingredients.some((item) => item.name === 'cobblestone' && item.missing === 6)));
  assert.equal((await run('inspect_recipe', { item_name: 'not_a_real_item' })).error.code, 'UNKNOWN_ITEM');
  assert.equal(JSON.stringify(bot.inventory.items()), inventory);
});

test('真实合成世界的交互站位遵守障碍、危险支撑、范围和截断，不修改游戏状态', async (t) => {
  const { bot, run } = querySetup(t);
  const World = require('prismarine-world')(bot.registry), Chunk = require('prismarine-chunk')(bot.registry);
  bot.world = new World().sync;
  const chunk = new Chunk({ minY: -64, worldHeight: 384 }); bot.world.setColumn(0, 0, chunk);
  bot.blockAt = (p) => bot.world.getBlock(p.floored());
  const set = (name, x, y, z) => chunk.setBlockStateId(new Vec3(x, y, z), bot.registry.blocksByName[name].defaultState);
  for (let x = 3; x <= 13; x++) for (let z = 3; z <= 13; z++) set('stone', x, 63, z);
  set('iron_ore', 10, 64, 8); set('lava', 8, 63, 8); set('stone', 9, 65, 8);
  const target = { x: 10, y: 64, z: 8 };
  const result = await run('find_interaction_positions', { position: target, max_results: 1 });
  assert.equal(result.success, true); assert.equal(result.data.candidates.length, 1); assert.equal(result.data.truncated, true);
  const p = result.data.candidates[0].position;
  assert.notDeepEqual(p, { x: 8.5, y: 64, z: 8.5 });
  assert.notDeepEqual(p, { x: 9.5, y: 64, z: 8.5 });
  assert.ok(result.data.candidates[0].interactionDistance <= 4.5);
  assert.equal(result.data.pathVerified, false); assert.ok(result.data.scanned <= 405);
  assert.equal(bot.blockAt(new Vec3(10, 64, 8)).name, 'iron_ore');
  assert.equal((await run('find_interaction_positions', { position: { x: 100, y: 64, z: 8 } })).error.code, 'OUT_OF_RANGE');
  bot.blockAt = () => null;
  assert.equal((await run('find_interaction_positions', { position: target })).error.code, 'CHUNK_UNAVAILABLE');
});

test('熔炼配方查询与原执行器共用原料燃料定义，查询不会打开窗口或投入材料', async (t) => {
  const { bot, run } = querySetup(t);
  bot.openFurnace = () => { throw new Error('read must not open window'); };
  const data = (await run('inspect_smelting_recipe', { item_name: 'raw_iron', count: 8 })).data;
  assert.deepEqual(data.output, { name: 'iron_ingot', count: 8 });
  assert.equal(data.input.missing, 8);
  assert.equal(data.fuels.find((item) => item.name === 'coal').count, 1);
  assert.equal((await run('inspect_smelting_recipe', { item_name: 'diamond' })).error.code, 'UNSUPPORTED_RECIPE');
});

test('熔炼配方与旧执行接口的原料上限统一为64，弱燃料总需求可超过单槽容量', async (t) => {
  const { bot, run } = querySetup(t);
  const before = JSON.stringify(bot.inventory.items());
  bot.openFurnace = () => { throw new Error('read must not open window'); };
  const nine = await run('inspect_smelting_recipe', { item_name: 'raw_iron', count: 9 });
  assert.equal(nine.success, true);
  assert.equal(nine.data.fuels.find((item) => item.name === 'coal').count, 2);
  const full = await run('inspect_smelting_recipe', { item_name: 'raw_iron', count: 64 });
  assert.deepEqual(full.data.output, { name: 'iron_ingot', count: 64 });
  assert.equal(full.data.fuels.find((item) => item.name === 'coal').count, 8);
  assert.equal(full.data.fuels.find((item) => item.name === 'bamboo').count, 256);
  assert.equal((await run('inspect_smelting_recipe', { item_name: 'raw_iron', count: 65 })).success, false);
  assert.equal((await run('smelt_item', { position: { x: 1, y: 64, z: 1 }, action: 'start', item_name: 'raw_iron', count: 65 })).success, false);
  assert.equal(JSON.stringify(bot.inventory.items()), before);
});
