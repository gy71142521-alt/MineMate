const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { Vec3 } = require('vec3');
const { createToolSystem } = require('../src/tools');
function fixture() {
  const bot = new EventEmitter(), blocks = new Map(), inventory = [];
  bot.entity = { id: 1, isValid: true, position: new Vec3(0, 64, 0), height: 1.8 };
  bot.entities = { 1: bot.entity };
  bot.isAlive = true; bot.health = 20; bot.food = 15; bot.game = { minY: -64, height: 384 };
  bot.inventory = { items: () => inventory.filter((item) => item.count > 0), emptySlotCount: () => 20 };
  bot.registry = { blocksByName: { stone: { id: 1 }, dirt: { id: 2 } }, itemsByName: {} };
  bot.blockAt = (p) => blocks.get(p.toString()) ?? { name: 'air', position: p.clone(), boundingBox: 'empty', hardness: 0, canHarvest: () => true };
  bot.equip = async (item) => { bot.heldItem = item; };
  bot.canDigBlock = () => true; bot.canSeeBlock = () => true; bot.stopDigging = () => {};
  bot.clearControlStates = () => {};
  bot.dig = async (block) => blocks.delete(block.position.toString());
  bot.placeBlock = async (reference, face) => { setBlock(bot.heldItem.name, reference.position.plus(face)); };
  bot.pathfinder = { goal: null, setGoal(goal) { this.goal = goal; } };
  const setBlock = (name, p = new Vec3(1, 64, 0), extra = {}) => {
    const block = { name, position: p.clone(), boundingBox: 'block', hardness: 1, canHarvest: () => true, ...extra };
    blocks.set(p.toString(), block); return block;
  };
  const addItem = (name, count = 1, extra = {}) => {
    const item = { name, count, type: inventory.length + 1, stackSize: 64, metadata: 0, ...extra };
    inventory.push(item); bot.registry.itemsByName[name] = { id: item.type }; return item;
  };
  const movement = { stop: () => ({ ok: true }), follow: () => ({ ok: true }), goTo: () => ({ ok: true }) };
  const tools = createToolSystem(bot, { movement, logger: { log() {}, error() {} } });
  bot.emit('spawn');
  const run = (name, args) => tools.execute(name, args, 'Steve');
  return { bot, tools, run, setBlock, addItem, inventory, blocks };
}
test('挖掘仅作用指定方块，验证真实空气及工具缺失、耐久、距离和可见性', async () => {
  const f = fixture();
  const position = { x: 1, y: 64, z: 0 };
  f.setBlock('stone');
  assert.equal((await f.run('mine_block', { block_name: 'stone', position })).data.status, 'completed');
  f.setBlock('dirt');
  assert.equal((await f.run('mine_block', { block_name: 'stone', position })).error.code, 'BLOCK_CHANGED');
  f.setBlock('stone', new Vec3(1, 64, 0), { canHarvest: (type) => type === 5 });
  assert.equal((await f.run('mine_block', { block_name: 'stone', position })).error.code, 'TOOL_MISSING');
  f.addItem('pickaxe', 1, { type: 5, maxDurability: 10, durabilityUsed: 9 });
  assert.equal((await f.run('mine_block', { block_name: 'stone', position })).error.code, 'TOOL_MISSING');
  f.inventory[0].durabilityUsed = 0;
  f.bot.canSeeBlock = () => false;
  assert.equal((await f.run('mine_block', { block_name: 'stone', position })).error.code, 'NO_LINE_OF_SIGHT');
  assert.equal((await f.run('mine_block', { block_name: 'stone', position: { ...position, x: 100 } })).error.code, 'OUT_OF_RANGE');
});
test('挖掘 API 返回但方块未消失时只能反馈 uncertain', async () => {
  const f = fixture(); f.setBlock('stone'); f.bot.dig = async () => {};
  const result = await f.run('mine_block', { block_name: 'stone', position: { x: 1, y: 64, z: 0 } });
  assert.equal(result.data.status, 'uncertain'); assert.equal(result.data.pickupVerified, false);
});
test('方块有限查找校验名称、三维半径、返回上限', async () => {
  const f = fixture(); let options;
  f.bot.findBlocks = (o) => { options = o; return [new Vec3(1, 64, 0), new Vec3(2, 64, 0), new Vec3(1, 80, 0)]; };
  const result = await f.run('find_block', { block_name: 'stone', radius: 5, max_results: 1 });
  assert.equal(options.count, 2); assert.equal(options.maxDistance, 5);
  assert.equal(result.data.blocks.length, 1); assert.equal(result.data.truncated, true);
  assert.equal((await f.run('find_block', { block_name: 'fake' })).error.code, 'UNKNOWN_BLOCK');
});
test('放置检查支撑面、占用和背包，单方块放置后读取真实结果', async () => {
  const f = fixture(); f.setBlock('stone', new Vec3(1, 63, 0));
  const args = { block_name: 'dirt', position: { x: 1, y: 64, z: 0 }, reference_position: { x: 1, y: 63, z: 0 }, face: 'up' };
  assert.equal((await f.run('inspect_placement', args)).data.placeable, true);
  assert.equal((await f.run('place_block', args)).error.code, 'ITEM_MISSING');
  f.addItem('dirt');
  assert.equal((await f.run('place_block', args)).data.status, 'completed');
  assert.equal((await f.run('place_block', args)).error.code, 'OCCUPIED');
  assert.equal((await f.run('inspect_placement', { ...args, position: { x: 2, y: 64, z: 0 } })).error.code, 'INVALID_FACE');
});
test('拾取须验证背包增量；实体消失不能假定拾取成功；取消清除路径', async () => {
  for (const success of [true, false]) {
    const f = fixture();
    const entity = { id: 2, isValid: true, position: new Vec3(1, 64, 0), getDroppedItem: () => ({ name: 'stone' }) };
    f.bot.entities[2] = entity;
    const pending = f.run('collect_item', { item_name: 'stone' });
    if (success) f.addItem('stone', 2); else entity.isValid = false;
    const result = await pending;
    assert.equal(result.data.status, success ? 'completed' : 'uncertain');
    assert.equal(f.bot.pathfinder.goal, null);
  }
  const f = fixture();
  assert.equal((await f.run('collect_item', { item_name: 'stone' })).error.code, 'ITEM_ENTITY_MISSING');
});

test('实体查找使用三维距离并排除自身、无效对象和超过数量上限的实体', async () => {
  const f = fixture();
  f.bot.entities[2] = { id: 2, isValid: true, name: 'zombie', type: 'mob', position: new Vec3(1, 64, 0) };
  f.bot.entities[3] = { id: 3, isValid: true, name: 'zombie', position: new Vec3(0, 90, 0) };
  f.bot.entities[4] = { id: 4, isValid: false, name: 'zombie', position: new Vec3(1, 64, 0) };
  const result = await f.run('find_entity', { entity_type: 'zombie', radius: 5, max_results: 1 });
  assert.equal(result.data.entities.length, 1); assert.equal(result.data.entities[0].id, 2);
});
test('攻击拒绝玩家和中立生物，目标失效停止，并按观察结果区分击杀', async () => {
  const f = fixture(); f.bot.lookAt = async () => {}; f.bot.entityAtCursor = () => f.bot.entities[2];
  for (const name of ['player', 'cow', 'enderman']) {
    f.bot.entities[2] = { id: 2, isValid: true, name, type: name === 'player' ? 'player' : 'mob', position: new Vec3(1, 64, 0) };
    assert.equal((await f.run('attack_entity', { entity_id: 2 })).error.code, 'FORBIDDEN_TARGET');
  }
  const zombie = { id: 2, name: 'zombie', type: 'mob', isValid: true, position: new Vec3(1, 64, 0) };
  f.bot.entities[2] = zombie; let hits = 0;
  f.bot.attack = (entity) => { hits++; f.bot.emit('entityDead', entity); entity.isValid = false; };
  const result = await f.run('attack_entity', { entity_id: 2, max_hits: 3 });
  assert.equal(hits, 1); assert.equal(result.data.deathObserved, true);
  assert.equal(f.bot.listenerCount('entityDead'), 0);
  assert.equal((await f.run('attack_entity', { entity_id: 2 })).error.code, 'ENTITY_MISSING');
});
test('装备验证手持，食用校验类型与数量变化，use_item 不盲用所有道具', async () => {
  const f = fixture(); f.addItem('apple', 2); f.addItem('stone');
  f.bot.registry.foodsByName = { apple: {} };
  f.bot.deactivateItem = () => {};
  f.bot.consume = async () => { f.inventory[0].count--; f.bot.food += 4; };
  assert.equal((await f.run('equip_item', { item_name: 'apple', destination: 'hand' })).data.status, 'completed');
  assert.equal((await f.run('consume_item', { item_name: 'apple' })).data.after.count, 1);
  assert.equal((await f.run('use_item', { item_name: 'stone' })).error.code, 'UNSUPPORTED_ITEM');
  f.bot.food = 20;
  assert.equal((await f.run('consume_item', { item_name: 'apple' })).error.code, 'CANNOT_CONSUME');
});
test('丢弃严格限制数量和白名单，拒绝清空整个背包，并验证实际增减', async () => {
  const f = fixture(); f.addItem('dirt', 5); f.addItem('diamond');
  assert.equal((await f.run('drop_item', { item_name: 'diamond', count: 1 })).error.code, 'PROTECTED_ITEM');
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: 17 })).error.code, 'INVALID_ARGUMENT');
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: 6 })).error.code, 'INSUFFICIENT_ITEMS');
  f.bot.toss = async (_, __, count) => { f.inventory[0].count -= count; };
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: 2 })).data.dropped, 2);
  f.inventory[1].count = 0;
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: 3 })).error.code, 'EMPTY_INVENTORY_FORBIDDEN');
});

function windowFixture(f, name, window) {
  f.setBlock(name);
  let closed = 0;
  f.bot.closeWindow = async (value) => { assert.equal(value, window); f.bot.currentWindow = null; closed++; };
  const open = async () => { f.bot.currentWindow = window; return window; };
  f.bot.openBlock = open; f.bot.openFurnace = open; f.bot.openContainer = open;
  return () => closed;
}
test('工作台打开验证类型并关闭，错误及取消后迟到窗口也关闭', async () => {
  const f = fixture(), position = { x: 1, y: 64, z: 0 };
  const win = { type: 'minecraft:crafting' }, closed = windowFixture(f, 'crafting_table', win);
  assert.equal((await f.run('open_workbench', { position })).data.type, 'workbench'); assert.equal(closed(), 1);
  win.type = 'minecraft:chest';
  assert.equal((await f.run('open_workbench', { position })).error.code, 'WRONG_WINDOW'); assert.equal(closed(), 2);
  let release;
  f.bot.openBlock = () => new Promise((resolve) => { release = () => { f.bot.currentWindow = win; resolve(win); }; });
  const pending = f.run('open_workbench', { position });
  f.tools.cancel('Steve'); assert.equal((await pending).error.code, 'CANCELLED');
  release(); await new Promise(setImmediate); assert.equal(closed(), 3); assert.equal(f.bot.currentWindow, null);
});
test('合成区分无配方与材料不足，并验证真实产物数量', async () => {
  const f = fixture(); const item = f.addItem('stick', 0);
  f.bot.recipesAll = () => [];
  assert.equal((await f.run('craft_item', { item_name: 'stick', count: 4 })).error.code, 'NO_RECIPE');
  f.bot.recipesAll = () => [{ result: { count: 4 } }]; f.bot.recipesFor = () => [];
  assert.equal((await f.run('craft_item', { item_name: 'stick', count: 4 })).error.code, 'MATERIALS_MISSING');
  f.bot.recipesFor = () => f.bot.recipesAll();
  f.bot.craft = async (_, count) => { assert.equal(count, 1); item.count += 4; };
  const result = await f.run('craft_item', { item_name: 'stick', count: 5 });
  assert.equal(result.data.expected, 8); assert.equal(result.data.actualIncrease, 8); assert.equal(result.data.status, 'completed');
});
test('熔炉原料/燃料不足不启动，投入返回 pending，未完成不可取出，完成后验证产物', async () => {
  const f = fixture(), position = { x: 1, y: 64, z: 0 };
  const raw = f.addItem('raw_iron', 2), coal = f.addItem('coal', 1), product = f.addItem('iron_ingot', 0);
  const slots = [null, null, null];
  const win = { fuel: 0, progress: 0, inputItem: () => slots[0], fuelItem: () => slots[1], outputItem: () => slots[2],
    putInput: async (_, __, n) => { raw.count -= n; slots[0] = { name: 'raw_iron', count: n }; },
    putFuel: async (_, __, n) => { coal.count -= n; slots[1] = { name: 'coal', count: n }; },
    takeOutput: async () => { product.count += slots[2].count; slots[2] = null; }
  };
  const closed = windowFixture(f, 'furnace', win);
  assert.equal((await f.run('smelt_item', { position, action: 'start', item_name: 'raw_iron', fuel_name: 'coal', count: 3 })).error.code, 'MATERIALS_MISSING');
  const start = await f.run('smelt_item', { position, action: 'start', item_name: 'raw_iron', fuel_name: 'coal', count: 2 });
  assert.equal(start.data.status, 'pending'); assert.equal(closed(), 1);
  assert.equal((await f.run('smelt_item', { position, action: 'status', item_name: 'iron_ingot', count: 2 })).data.outputReady, false);
  assert.equal((await f.run('smelt_item', { position, action: 'take', item_name: 'iron_ingot', count: 2 })).error.code, 'OUTPUT_NOT_READY');
  slots[0] = null; slots[2] = { name: 'iron_ingot', count: 2 };
  assert.equal((await f.run('smelt_item', { position, action: 'status', item_name: 'iron_ingot', count: 2 })).data.outputReady, true);
  assert.equal((await f.run('smelt_item', { position, action: 'take', item_name: 'iron_ingot', count: 2 })).data.taken, 2);
  assert.equal(f.bot.currentWindow, null);
});
test('箱子存取验证双方增减，数量不足不执行，异常关闭窗口', async () => {
  const f = fixture(), position = { x: 1, y: 64, z: 0 }, dirt = f.addItem('dirt', 8);
  const slots = [null, null]; f.bot.inventory.slots = [dirt, null];
  const win = { inventoryStart: 2, slots,
    deposit: async (_, __, n) => { dirt.count -= n; slots[0] = { name: 'dirt', count: (slots[0]?.count ?? 0) + n, stackSize: 64 }; },
    withdraw: async (_, __, n) => { dirt.count += n; slots[0].count -= n; }
  };
  const closed = windowFixture(f, 'chest', win);
  assert.equal((await f.run('transfer_item', { position, item_name: 'dirt', direction: 'deposit', count: 3 })).data.inventoryDelta, -3);
  assert.equal((await f.run('transfer_item', { position, item_name: 'dirt', direction: 'withdraw', count: 2 })).data.containerDelta, -2);
  assert.equal((await f.run('transfer_item', { position, item_name: 'dirt', direction: 'withdraw', count: 2 })).error.code, 'INSUFFICIENT_ITEMS');
  win.deposit = async () => { throw new Error('unit-secret'); };
  const result = await f.run('transfer_item', { position, item_name: 'dirt', direction: 'deposit', count: 1 });
  assert.equal(result.success, false); assert.doesNotMatch(JSON.stringify(result), /unit-secret/); assert.equal(closed(), 4);
});

test('窗口打开时使用窗口背包槽验证存取，不把旧 bot.inventory 数量当作实时状态', async () => {
  const f = fixture(), position = { x: 1, y: 64, z: 0 }, dirt = f.addItem('dirt', 8);
  const slots = [null, null, { ...dirt }, null];
  f.bot.inventory.slots = [dirt, null];
  const win = { inventoryStart: 2, inventoryEnd: 4, slots, deposit: async (_, __, n) => { slots[2].count -= n; slots[0] = { ...dirt, count: n }; } };
  windowFixture(f, 'chest', win);
  const result = await f.run('transfer_item', { position, direction: 'deposit', item_name: 'dirt', count: 3 });
  assert.equal(result.data.status, 'completed'); assert.equal(result.data.inventoryDelta, -3);
  assert.equal(dirt.count, 8);
});

test('use_item 为盾牌和剪刀选择不同 API，不虚报使用效果', async () => {
  const f = fixture(); f.addItem('shield'); f.addItem('shears');
  let activated = 0, deactivated = 0, used = 0;
  f.bot.activateItem = () => { activated++; };
  f.bot.deactivateItem = () => { deactivated++; };
  assert.equal((await f.run('use_item', { item_name: 'shield' })).data.status, 'uncertain');
  assert.equal(activated, 1); assert.equal(deactivated, 1);
  f.bot.lookAt = async () => {};
  f.bot.entities[2] = { id: 2, isValid: true, name: 'sheep', position: new Vec3(1, 64, 0), metadata: [] };
  f.bot.useOn = (entity) => { assert.equal(entity.id, 2); used++; };
  assert.equal((await f.run('use_item', { item_name: 'shears', entity_id: 2 })).data.status, 'uncertain');
  assert.equal(used, 1);
});
