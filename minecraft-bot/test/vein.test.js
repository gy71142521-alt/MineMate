const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { Vec3 } = require('vec3');
const { createToolSystem } = require('../src/tools');

function worldFixture(t) {
  const bot = new EventEmitter();
  bot.registry = require('prismarine-registry')('1.21.4');
  const World = require('prismarine-world')(bot.registry);
  const Chunk = require('prismarine-chunk')(bot.registry);
  bot.world = new World().sync;
  for (let x = -1; x <= 1; x++) for (let z = -1; z <= 1; z++) {
    const chunk = new Chunk({ minY: -64, worldHeight: 384 });
    bot.world.setColumn(x, z, chunk);
  }
  bot.entity = { id: 1, position: new Vec3(0.5, 64, 0.5), eyeHeight: 1.62 };
  bot.entities = { 1: bot.entity }; bot.isAlive = true;
  bot.game = { minY: -64, height: 384, dimension: 'overworld' };
  const inventory = [{ name: 'iron_pickaxe', type: bot.registry.itemsByName.iron_pickaxe.id,
    count: 1, maxDurability: 250, durabilityUsed: 0 }];
  bot.inventory = { items: () => inventory, emptySlotCount: () => 30 };
  bot.blockAt = p => bot.world.getBlock(p.floored());
  const set = (name, x, y, z) => bot.world.setBlockStateId(new Vec3(x, y, z), bot.registry.blocksByName[name].minStateId);
  for (let x = -10; x <= 12; x++) for (let z = -10; z <= 10; z++) set('stone', x, 63, z);
  bot.canSeeBlock = b => {
    const eye = bot.entity.position.offset(0, 1.62, 0), end = b.position.offset(0.5, 0.5, 0.5);
    return bot.world.raycast(eye, end.minus(eye).normalize(), eye.distanceTo(end) + 0.01)?.position.equals(b.position) ?? false;
  };
  bot.canDigBlock = b => b.diggable && b.position.distanceTo(bot.entity.position) < 5;
  bot.equip = async item => { bot.heldItem = item; };
  bot.unequip = async () => { bot.heldItem = null; };
  const dug = [];
  bot.dig = async b => { dug.push(b.position.clone()); set('air', b.position.x, b.position.y, b.position.z); };
  bot.stopDigging = () => {};
  const tools = createToolSystem(bot, { movement: { stop: () => ({ ok: true }) }, logger: { log() {}, error() {} } });
  bot.emit('spawn'); t.after(() => tools.dispose());
  return { bot, set, dug, inventory, tools, run: (name, args) => tools.execute(name, args, 'Steve') };
}

test('矿脉邻接查询只返回种子及新暴露六邻接矿石，不读取整片埋藏矿脉', async t => {
  const f = worldFixture(t);
  f.set('iron_ore', 2, 64, 0); f.set('iron_ore', 3, 64, 0); f.set('iron_ore', 4, 64, 0);
  const result = await f.run('inspect_vein', { position: { x: 2, y: 64, z: 0 }, block_name: 'iron_ore' });
  assert.deepEqual(result.data.blocks.map(b => b.position.x), [2, 3]);
  assert.equal(f.dug.length, 0);
  assert.equal((await f.run('inspect_vein', { position: { x: 2, y: 64, z: 0 }, block_name: 'stone' })).error.code, 'NOT_ORE');
});

test('只读通道规划优先现有通路，并保持全局寻路配置不变', async t => {
  const f = worldFixture(t);
  f.bot.pathfinder = { movements: { canDig: false } };
  f.set('iron_ore', 7, 64, 0);
  const r = await f.run('inspect_mining_route', { position: { x: 7, y: 64, z: 0 }, block_name: 'iron_ore' });
  assert.equal(r.success, true);
  assert.equal(r.data.next.action, 'move');
  assert.equal(r.data.clearCount, 0);
  assert.equal(f.dug.length, 0);
  assert.equal(f.bot.pathfinder.movements.canDig, false);
});

test('两格通道单步挖掘后重新规划，最终只清除必需障碍并接近矿石', async t => {
  const f = worldFixture(t);
  // 窄走廊的两侧为不能清障的基岩，隔断两格高；可直接看到的矿石先不自动采。
  for (let x = -1; x <= 8; x++) for (const z of [-1, 1]) for (let y = 64; y <= 68; y++) f.set('bedrock', x, y, z);
  for (let x = -1; x <= 8; x++) f.set('bedrock', x, 67, 0);
  f.set('stone', 2, 64, 0); f.set('stone', 2, 65, 0); f.set('iron_ore', 6, 64, 0);
  const args = { position: { x: 6, y: 64, z: 0 }, block_name: 'iron_ore', radius: 8, max_clear_blocks: 4 };
  assert.equal((await f.run('inspect_mining_route', { ...args, max_clear_blocks: 0 })).data.reachable, false);
  let ready = false;
  for (let i = 0; i < 12; i++) {
    const route = await f.run('inspect_mining_route', args);
    assert.equal(route.success, true, JSON.stringify(route));
    assert.equal(route.data.reachable, true, JSON.stringify(route));
    const step = route.data.next;
    if (step.action === 'ready') { ready = true; break; }
    if (step.action === 'dig') {
      const r = await f.run('mine_route_block', args);
      assert.equal(r.data?.blockRemoved, true, JSON.stringify(r));
    } else f.bot.entity.position = new Vec3(step.position.x + 0.5, step.position.y, step.position.z + 0.5);
  }
  assert.equal(ready, true);
  assert.ok(f.dug.length >= 1 && f.dug.length <= 2);
  assert.ok(f.dug.every(p => p.x === 2 && p.z === 0));
  assert.equal(f.bot.blockAt(new Vec3(6, 64, 0)).name, 'iron_ore');
});

test('中心视线受阻但矿石上表面可见时，采用官方 raycast 挖掘面', async t => {
  const f = worldFixture(t);
  f.set('stone', 2, 64, 0); f.set('iron_ore', 3, 64, 0);
  const target = f.bot.blockAt(new Vec3(3, 64, 0));
  assert.equal(f.bot.canSeeBlock(target), false);
  let face;
  const originalDig = f.bot.dig;
  f.bot.dig = async (block, forceLook, digFace) => { face = digFace; await originalDig(block); };
  const r = await f.run('mine_vein_block', { position: { x: 3, y: 64, z: 0 }, block_name: 'iron_ore' });
  assert.equal(r.data?.blockRemoved, true, JSON.stringify(r));
  assert.equal(face, 'raycast');
  assert.equal(f.bot.blockAt(new Vec3(2, 64, 0)).name, 'stone');
});

test('矿脉路线拒绝缺工具、液体、坠落物及脚下矿石，预算0不清障', async t => {
  for (const hazard of ['lava', 'water', 'gravel']) {
    const f = worldFixture(t);
    f.set('iron_ore', 2, 64, 0); f.set(hazard, 2, 65, 0);
    const r = await f.run('mine_vein_block', { position: { x: 2, y: 64, z: 0 }, block_name: 'iron_ore' });
    assert.equal(r.error.code, 'UNSAFE_MINING_TARGET'); assert.equal(f.dug.length, 0);
  }
  const f = worldFixture(t);
  f.set('iron_ore', 2, 64, 0); f.inventory.length = 0;
  assert.equal((await f.run('inspect_mining_route', { position: { x: 2, y: 64, z: 0 }, block_name: 'iron_ore' })).error.code, 'TOOL_MISSING');
  const g = worldFixture(t);
  g.set('iron_ore', 0, 63, 0);
  assert.equal((await g.run('mine_vein_block', { position: { x: 0, y: 63, z: 0 }, block_name: 'iron_ore' })).error.code, 'UNSAFE_MINING_TARGET');
});

test('掉落物通道规划可在不可达时返回有界诊断，区块未知不能当空气', async t => {
  const f = worldFixture(t);
  for (let z = -10; z <= 10; z++) for (let y = 64; y <= 68; y++) f.set('bedrock', 2, y, z);
  const r = await f.run('inspect_mining_route', { position: { x: 4, y: 64, z: 0 }, purpose: 'pickup', radius: 5, max_clear_blocks: 0 });
  assert.equal(r.data.reachable, false); assert.ok(r.data.expanded <= 512);
  const original = f.bot.blockAt;
  f.bot.blockAt = p => p.x > 2 ? null : original(p);
  assert.equal((await f.run('inspect_mining_route', { position: { x: 4, y: 64, z: 0 }, purpose: 'pickup' })).error.code, 'CHUNK_UNAVAILABLE');
});

test('矿石近侧面在范围内但中心超出交互距离时，先移动再挖掘', async t => {
  const f = worldFixture(t);
  f.bot.entity.position = new Vec3(0.9, 64, 0.5);
  f.set('iron_ore', 5, 64, 0);
  const r = await f.run('inspect_mining_route', { position: { x: 5, y: 64, z: 0 }, block_name: 'iron_ore' });
  assert.equal(r.success, true);
  assert.equal(r.data.next.action, 'move');
  assert.equal(f.dug.length, 0);
});

test('异步装备工具期间出现液体时，挖掘前重新检查并拒绝操作', async t => {
  const f = worldFixture(t);
  f.set('iron_ore', 2, 64, 0);
  f.bot.equip = async item => { f.bot.heldItem = item; f.set('lava', 2, 65, 0); };
  const r = await f.run('mine_vein_block', { position: { x: 2, y: 64, z: 0 }, block_name: 'iron_ore' });
  assert.equal(r.error.code, 'UNSAFE_MINING_TARGET');
  assert.equal(f.dug.length, 0);
  assert.equal(f.bot.blockAt(new Vec3(2, 64, 0)).name, 'iron_ore');
});
