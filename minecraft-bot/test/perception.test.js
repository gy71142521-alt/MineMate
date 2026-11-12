const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { Vec3 } = require('vec3');
const registry = require('prismarine-registry')('1.21.4');
const { createPerception } = require('../src/perception');
const { readNearbyBlocks } = require('../src/perception/nearbyBlocks');

const ITEM = (name, count, slot) => ({ name, count, slot });

function setup(t, { config = {}, spawn = true } = {}) {
  const bot = new EventEmitter();
  const calls = { findBlocks: [], blockAt: [], mutations: [] };
  bot.registry = registry;
  bot.game = { dimension: 'overworld' };
  bot.entity = {
    id: 1,
    position: new Vec3(0.5, 64, 0.5),
    equipment: [ITEM('iron_sword', 1), ITEM('shield', 1), null, null, ITEM('iron_chestplate', 1), ITEM('iron_helmet', 1)]
  };
  bot.health = 17;
  bot.food = 13;
  bot.experience = { level: 9 };
  bot.isAlive = true;
  bot.players = {};
  bot.entities = { 1: bot.entity };
  bot.inventory = { slots: Array(46).fill(null) };
  bot.inventory.slots[9] = ITEM('oak_log', 5, 9);
  bot.inventory.slots[36] = ITEM('oak_log', 2, 36);
  bot.inventory.slots[45] = ITEM('shield', 1, 45);
  bot.blockNames = { '0,64,0': 'air' };
  bot.findBlocks = (options) => {
    calls.findBlocks.push(options);
    return bot.blockResults ?? [];
  };
  bot.blockAt = (position, extraInfos) => {
    calls.blockAt.push({ position: { x: position.x, y: position.y, z: position.z }, extraInfos });
    const name = bot.blockNames?.[`${Math.floor(position.x)},${Math.floor(position.y)},${Math.floor(position.z)}`];
    return name ? { name } : null;
  };
  for (const method of ['dig', 'placeBlock', 'equip', 'toss', 'setControlState', 'clearControlStates']) {
    bot[method] = () => calls.mutations.push(method);
  }
  const movement = { getState: () => ({ type: 'goto', taskId: 42 }) };
  const perception = createPerception(bot, {
    movement,
    config: { entityRadius: 16, blockRadius: 5, maxEntities: 32, maxBlocks: 64, blockCacheMs: 1000, ...config },
    logger: { error: (message) => calls.mutations.push(message) }
  });
  t.after(() => perception.dispose());
  if (spawn) bot.emit('spawn');
  return { bot, perception, calls };
}

function addEntity(bot, { id, type = 'mob', name, kind, position, isValid = true, username }) {
  const entity = { id, type, name, kind, position: new Vec3(...position), isValid, username };
  bot.entities[id] = entity;
  return entity;
}

function setBlocks(bot, entries) {
  bot.blockResults = entries.map(([name, x, y, z]) => {
    bot.blockNames ??= {};
    bot.blockNames[`${x},${y},${z}`] = name;
    return new Vec3(x, y, z);
  });
}

test('读取自身状态使用数值坐标、经验、维度、存活和只读移动摘要', (t) => {
  const { perception } = setup(t);
  const state = perception.getPlayerState();
  assert.deepEqual(state, {
    position: { x: 0.5, y: 64, z: 0.5 },
    health: 17, food: 13, experienceLevel: 9, dimension: 'overworld', alive: true,
    movement: { active: true, type: 'goto', taskId: 42 }
  });
  for (const value of Object.values(state.position)) assert.equal(typeof value, 'number');
});

test('背包忽略空槽、保留槽位，汇总指定物品且独立读取装备栏', (t) => {
  const { perception } = setup(t);
  const inventory = perception.getInventory();
  assert.equal(inventory.available, true);
  assert.deepEqual(inventory.items, [
    { name: 'oak_log', count: 5, slot: 9 },
    { name: 'oak_log', count: 2, slot: 36 },
    { name: 'shield', count: 1, slot: 45 }
  ]);
  assert.equal(perception.getItemCount('oak_log'), 7);
  assert.equal(perception.getItemCount('diamond'), 0);
  assert.deepEqual(inventory.equipment.map(({ slot, name }) => ({ slot, name })), [
    { slot: 'mainhand', name: 'iron_sword' },
    { slot: 'offhand', name: 'shield' },
    { slot: 'chest', name: 'iron_chestplate' },
    { slot: 'head', name: 'iron_helmet' }
  ]);
});

test('空背包是可用的空列表，汇总数量为零', (t) => {
  const { bot, perception } = setup(t);
  bot.inventory.slots.fill(null);
  assert.deepEqual(perception.getInventory(), { available: true, items: [], equipment: [
    { name: 'iron_sword', count: 1, slot: 'mainhand' },
    { name: 'shield', count: 1, slot: 'offhand' },
    { name: 'iron_chestplate', count: 1, slot: 'chest' },
    { name: 'iron_helmet', count: 1, slot: 'head' }
  ] });
  assert.equal(perception.getItemCount('oak_log'), 0);
});

test('附近实体分类、距离采用三维欧几里得值并排除自身、无效和超半径对象', (t) => {
  const { bot, perception } = setup(t);
  const player = addEntity(bot, { id: 2, type: 'player', username: 'Steve', position: [3.5, 68, 0.5] });
  const cow = addEntity(bot, { id: 3, type: 'mob', name: 'cow', kind: registry.entitiesByName.cow.category, position: [0.5, 64, 4.5] });
  const zombie = addEntity(bot, { id: 4, type: 'mob', name: 'zombie', kind: registry.entitiesByName.zombie.category, position: [0.5, 75, 0.5] });
  addEntity(bot, { id: 5, type: 'mob', name: 'chicken', kind: 'Passive mobs', position: [0.5, 64, 1.5], isValid: false });
  addEntity(bot, { id: 6, type: 'mob', name: 'sheep', kind: 'Passive mobs', position: [17, 64, 0.5] });
  const result = perception.getNearbyEntities();
  assert.equal(result.available, true);
  assert.equal(result.truncated, false);
  assert.deepEqual(result.items.map(({ id, category }) => ({ id, category })), [
    { id: cow.id, category: 'passive' },
    { id: player.id, category: 'player' },
    { id: zombie.id, category: 'hostile' }
  ]);
  assert.equal(result.items.find((entity) => entity.id === player.id).distance, 5);
  assert.equal(result.items.find((entity) => entity.id === zombie.id).distance, 11);
  assert.ok(result.items.every((entity) => entity.valid && entity.id !== bot.entity.id));
});

test('实体结果按最近距离排序、受数量上限约束并标记截断', (t) => {
  const { bot, perception } = setup(t, { config: { maxEntities: 2 } });
  addEntity(bot, { id: 2, type: 'player', username: 'Near', position: [1.5, 64, 0.5] });
  addEntity(bot, { id: 3, type: 'mob', name: 'cow', kind: 'Passive mobs', position: [2.5, 64, 0.5] });
  addEntity(bot, { id: 4, type: 'mob', name: 'zombie', kind: 'Hostile mobs', position: [3.5, 64, 0.5] });
  const result = perception.getNearbyEntities();
  assert.deepEqual(result.items.map((entity) => entity.id), [2, 3]);
  assert.equal(result.truncated, true);
});

test('附近方块只使用有限白名单和配置半径，返回带数值距离的最近方块', (t) => {
  const { bot, perception, calls } = setup(t, { config: { blockRadius: 4 } });
  setBlocks(bot, [['stone', 1, 64, 0], ['oak_log', 0, 64, 2]]);
  const result = perception.getNearbyBlocks();
  assert.equal(result.available, true);
  assert.deepEqual(result.items.map((block) => block.name), ['stone', 'oak_log']);
  assert.ok(result.items.every((block) => typeof block.distance === 'number'));
  assert.equal(calls.findBlocks.length, 1);
  assert.ok(calls.findBlocks[0].maxDistance >= 4 && calls.findBlocks[0].maxDistance < 4 + Math.sqrt(3));
  assert.ok(calls.findBlocks[0].count > 0 && calls.findBlocks[0].count <= 3375);
  assert.ok(result.items.every((block) => block.distance <= 4));
  assert.deepEqual(calls.findBlocks[0].matching, calls.findBlocks[0].matching.filter(Number.isFinite));
  assert.ok(calls.findBlocks[0].matching.length < 32);
  assert.ok(calls.blockAt.every((call) => call.extraInfos === false));
});

test('方块结果限制为最近项目并准确标记截断', (t) => {
  const { bot, perception, calls } = setup(t, { config: { maxBlocks: 2 } });
  setBlocks(bot, [['stone', 1, 64, 0], ['oak_log', 2, 64, 0], ['dirt', 3, 64, 0]]);
  const result = perception.getNearbyBlocks();
  assert.equal(result.items.length, 2);
  assert.equal(result.truncated, true);
  assert.ok(calls.findBlocks[0].count <= 3375);
});

test('Bot 在同一方块内移动、移动到相邻方块或切换维度后使环境缓存失效', (t) => {
  const { bot, perception, calls } = setup(t);
  setBlocks(bot, [['stone', 1, 64, 0]]);
  perception.getNearbyBlocks();
  perception.getNearbyBlocks();
  assert.equal(calls.findBlocks.length, 1);

  bot.entity.position.x = 0.75;
  perception.getNearbyBlocks();
  assert.equal(calls.findBlocks.length, 2);

  bot.entity.position.x = 1.5;
  bot.blockNames['1,64,0'] = 'air';
  perception.getNearbyBlocks();
  assert.equal(calls.findBlocks.length, 3);

  bot.game.dimension = 'the_nether';
  bot.emit('game');
  perception.getNearbyBlocks();
  assert.equal(calls.findBlocks.length, 4);
});

test('死亡、重生和断线使方块缓存失效或不可用', (t) => {
  const { bot, perception, calls } = setup(t);
  setBlocks(bot, [['stone', 1, 64, 0]]);
  perception.getNearbyBlocks();
  bot.isAlive = false;
  bot.emit('death');
  assert.deepEqual(perception.getNearbyBlocks(), { available: false, items: [], truncated: false });
  bot.isAlive = true;
  bot.emit('respawn');
  assert.equal(perception.getNearbyBlocks().available, false);
  bot.emit('spawn');
  assert.equal(perception.getNearbyBlocks().available, true);
  assert.equal(calls.findBlocks.length, 2);
  bot.emit('end');
  const snapshot = perception.getWorldSnapshot();
  assert.equal(snapshot.availability.nearbyBlocks, 'unavailable');
  assert.equal(snapshot.nearbyBlocks.length, 0);
});

test('未出生和断线时快照保持完全相同的顶层字段结构', (t) => {
  const { bot, perception } = setup(t, { spawn: false });
  const beforeSpawn = perception.getWorldSnapshot();
  assert.deepEqual(Object.keys(beforeSpawn), [
    'timestamp', 'availability', 'player', 'inventory', 'equipment',
    'nearbyEntities', 'nearbyBlocks', 'truncated', 'limits'
  ]);
  assert.deepEqual(beforeSpawn.availability, {
    player: 'unavailable', inventory: 'unavailable', nearbyEntities: 'unavailable', nearbyBlocks: 'unavailable'
  });
  assert.equal(beforeSpawn.player.position, null);
  assert.equal(beforeSpawn.player.experienceLevel, null);
  assert.deepEqual(beforeSpawn.inventory, []);
  assert.deepEqual(beforeSpawn.equipment, []);

  bot.emit('spawn');
  const connected = perception.getWorldSnapshot();
  bot.emit('end');
  const disconnected = perception.getWorldSnapshot();
  assert.deepEqual(Object.keys(disconnected), Object.keys(connected));
  assert.deepEqual(disconnected.availability, {
    player: 'unavailable', inventory: 'unavailable', nearbyEntities: 'unavailable', nearbyBlocks: 'unavailable'
  });
  assert.deepEqual(disconnected.truncated, { entities: false, blocks: false });
  assert.equal(typeof disconnected.timestamp, 'number');
});

test('读取快照不调用任何移动、挖掘、放置、装备或丢弃操作', (t) => {
  const { perception, calls } = setup(t);
  perception.getWorldSnapshot();
  perception.getWorldSnapshot();
  assert.deepEqual(calls.mutations, []);
});

test('背包或世界数据异常时字段保持可用结构，不泄漏异常对象', (t) => {
  const { bot, perception } = setup(t);
  bot.findBlocks = () => { throw new Error('world read failed'); };
  const snapshot = perception.getWorldSnapshot();
  assert.equal(snapshot.availability.nearbyBlocks, 'unavailable');
  assert.deepEqual(snapshot.nearbyBlocks, []);
  assert.equal(snapshot.availability.inventory, 'available');
});

test('缓存到期、方块更新和区块加载/卸载后重新读取，返回结果不共享缓存对象', (t) => {
  const { bot, perception, calls } = setup(t);
  setBlocks(bot, [['stone', 1, 64, 0]]);
  const cache = { value: null };
  const options = {
    ready: true, position: bot.entity.position, dimension: 'overworld', radius: 5,
    maximum: 64, cacheMs: 1000, cache
  };
  const first = readNearbyBlocks(bot, { ...options, now: 1000 });
  first.items[0].position.x = 999;
  const second = readNearbyBlocks(bot, { ...options, now: 1999 });
  assert.equal(second.items[0].position.x, 1);
  second.items[0].position.x = 888;
  assert.equal(readNearbyBlocks(bot, { ...options, now: 1999 }).items[0].position.x, 1);
  assert.equal(calls.findBlocks.length, 1);
  readNearbyBlocks(bot, { ...options, now: 2000 });
  assert.equal(calls.findBlocks.length, 2);

  perception.getNearbyBlocks();
  for (const event of ['blockUpdate', 'chunkColumnLoad', 'chunkColumnUnload']) {
    const count = calls.findBlocks.length;
    bot.emit(event);
    perception.getNearbyBlocks();
    assert.equal(calls.findBlocks.length, count + 1);
  }
});

test('实体坐标无效、仅垂直距离超范围和其他类型均正确处理', (t) => {
  const { bot, perception } = setup(t);
  addEntity(bot, { id: 2, name: 'cow', kind: 'Passive mobs', position: [0.5, 81, 0.5] });
  addEntity(bot, { id: 3, name: 'cow', kind: 'Passive mobs', position: [NaN, 64, 0.5] });
  addEntity(bot, { id: 4, type: 'other', name: 'item', position: [0.5, 65, 0.5] });
  assert.deepEqual(perception.getNearbyEntities().items.map(({ id, category, distance }) => ({ id, category, distance })), [
    { id: 4, category: 'other', distance: 1 }
  ]);
});

test('缺失经验、不可读移动状态和消失的 Bot 实体均返回明确的空值或不可用状态', (t) => {
  const { bot, perception } = setup(t);
  delete bot.experience;
  delete bot.game;
  const state = perception.getPlayerState();
  assert.equal(state.experienceLevel, null);
  assert.equal(state.dimension, null);
  bot.entity = undefined;
  const snapshot = perception.getWorldSnapshot();
  assert.ok(Object.values(snapshot.availability).every((value) => value === 'unavailable'));
  assert.equal(perception.getItemCount('oak_log'), null);
});

test('读取异常的移动状态不丢弃生命值等可读信息，并记录日志', (t) => {
  const { bot } = setup(t);
  const errors = [];
  const perception = createPerception(bot, {
    movement: { getState: () => { throw new Error('unavailable'); } },
    logger: { error: (message) => errors.push(message) }
  });
  t.after(() => perception.dispose());
  bot.emit('spawn');
  assert.equal(perception.getPlayerState().health, 17);
  assert.deepEqual(perception.getPlayerState().movement, { active: null, type: null, taskId: null });
  assert.ok(errors.every((message) => message.includes('移动状态读取失败')));
});

test('游戏数据与返回快照相互隔离，快照可序列化为 JSON', (t) => {
  const { bot, perception } = setup(t);
  addEntity(bot, { id: 2, type: 'player', username: 'Steve', position: [2, 64, 0] });
  setBlocks(bot, [['stone', 1, 64, 0]]);
  const before = JSON.stringify({ entity: bot.entity, inventory: bot.inventory, entities: bot.entities });
  const snapshot = perception.getWorldSnapshot();
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot)), snapshot);
  snapshot.player.position.x = 999;
  snapshot.inventory[0].count = 999;
  snapshot.equipment[0].count = 999;
  snapshot.nearbyEntities[0].position.x = 999;
  snapshot.nearbyBlocks[0].position.x = 999;
  assert.equal(JSON.stringify({ entity: bot.entity, inventory: bot.inventory, entities: bot.entities }), before);
});

test('被踢后仍可查询稳定的不可用快照，所有感知监听器正确清理', (t) => {
  const { bot, perception } = setup(t);
  const keys = Object.keys(perception.getWorldSnapshot());
  bot.emit('kicked');
  const snapshot = perception.getWorldSnapshot();
  assert.deepEqual(Object.keys(snapshot), keys);
  assert.ok(Object.values(snapshot.availability).every((value) => value === 'unavailable'));
  for (const event of ['spawn', 'respawn', 'death', 'game', 'end', 'kicked', 'blockUpdate', 'chunkColumnLoad', 'chunkColumnUnload']) {
    assert.equal(bot.listenerCount(event), 0);
  }
  assert.doesNotThrow(() => perception.dispose());
});

test('区块尚未加载时环境不可用，不把缺失世界数据当作空资源列表', (t) => {
  const { bot, perception, calls } = setup(t);
  bot.blockAt = () => null;
  assert.equal(perception.getWorldSnapshot().availability.nearbyBlocks, 'unavailable');
  assert.equal(calls.findBlocks.length, 0);
});

test('实际 Mineflayer 方块 API 在合成区块中遵守三维半径、最近排序与数量限制', (t) => {
  const { bot, perception, calls } = setup(t, { config: { blockRadius: 2, maxBlocks: 2 } });
  const World = require('prismarine-world')(registry);
  const Chunk = require('prismarine-chunk')(registry);
  bot.game = { dimension: 'overworld', minY: -64, height: 384 };
  bot.entity.position = new Vec3(8.9, 64, 8.9);
  bot._client = new EventEmitter();
  bot._client.write = () => calls.mutations.push('packet');
  bot.world = new World().sync;
  const chunk = new Chunk({ minY: -64, worldHeight: 384 });
  bot.world.setColumn(0, 0, chunk);
  for (const [name, point] of [
    ['stone', new Vec3(8, 64, 8)], ['oak_log', new Vec3(10, 64, 9)],
    ['dirt', new Vec3(10, 64, 10)], ['coal_ore', new Vec3(9, 67, 9)]
  ]) chunk.setBlockStateId(point, registry.blocksByName[name].defaultState);
  require('mineflayer/lib/plugins/blocks')(bot, {});
  const result = perception.getNearbyBlocks();
  assert.equal(result.available, true);
  assert.deepEqual(result.items.map((block) => block.name), ['oak_log', 'stone']);
  assert.ok(result.items.every((block) => block.distance <= 2));
  assert.equal(result.truncated, true);
  assert.deepEqual(calls.mutations, []);
});
