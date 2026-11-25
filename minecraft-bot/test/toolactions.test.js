const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { Vec3 } = require('vec3');
const { createToolSystem } = require('../src/tools');
function fixture({ movement: suppliedMovement } = {}) {
  const bot = new EventEmitter(), blocks = new Map(), inventory = [];
  bot.entity = { id: 1, isValid: true, position: new Vec3(0, 64, 0), height: 1.8 };
  bot.entities = { 1: bot.entity };
  bot.isAlive = true; bot.health = 20; bot.food = 15; bot.game = { minY: -64, height: 384 };
  bot.inventory = { items: () => inventory.filter((item) => item.count > 0), emptySlotCount: () => 20 };
  bot.registry = { blocksByName: { stone: { id: 1 }, dirt: { id: 2 } }, itemsByName: {} };
  bot.blockAt = (p) => blocks.get(p.toString()) ?? { name: 'air', position: p.clone(), boundingBox: 'empty', hardness: 0, canHarvest: () => true };
  bot.equip = async (item) => { bot.heldItem = item; };
  bot.unequip = async () => { bot.heldItem = null; };
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
  const movement = suppliedMovement ?? { stop: () => ({ ok: true }), follow: () => ({ ok: true }), goTo: () => ({ ok: true }) };
  const tools = createToolSystem(bot, { movement, logger: { log() {}, error() {} } });
  bot.emit('spawn');
  const run = (name, args) => tools.execute(name, args, 'Steve');
  return { bot, tools, run, setBlock, addItem, inventory, blocks, movement };
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
test('精确站位导航拒绝实心脚部位置，避免反复 NAVIGATION_FAILED', async () => {
  const f = fixture();
  f.setBlock('dirt', new Vec3(1, 64, 0));
  const result = await f.run('goto_position', { x: 1, y: 64, z: 0, arrival_range: 0 });
  assert.equal(result.success, false);
  assert.equal(result.error.code, 'TARGET_OCCUPIED');
});

test('挖掘 API 返回但方块未消失时只能反馈 uncertain', async () => {
  const f = fixture(); f.setBlock('stone'); f.bot.dig = async () => {};
  const result = await f.run('mine_block', { block_name: 'stone', position: { x: 1, y: 64, z: 0 } });
  assert.equal(result.data.status, 'uncertain'); assert.equal(result.data.pickupVerified, false);
});

test('单点方块查询只读；指定钻石斧挖掘时验证并装备实际指定工具', async () => {
  const f = fixture(), position = { x: 1, y: 64, z: 0 };
  const block = f.setBlock('oak_log');
  const before = [...f.blocks.entries()];
  const inspected = await f.run('inspect_block', { position });
  assert.equal(inspected.data.name, 'oak_log');
  assert.equal(inspected.data.visible, true);
  assert.deepEqual([...f.blocks.entries()], before);
  assert.equal((await f.run('mine_block', { block_name: 'oak_log', position, tool_name: 'diamond_axe' })).error.code, 'TOOL_MISSING');
  const axe = f.addItem('diamond_axe', 1, { maxDurability: 1561, durabilityUsed: 0 });
  assert.equal((await f.run('mine_block', { block_name: 'oak_log', position, tool_name: 'diamond_axe' })).data.status, 'completed');
  assert.equal(f.bot.heldItem, axe);
  f.setBlock('stone', block.position, { canHarvest: () => false });
  assert.equal((await f.run('mine_block', { block_name: 'stone', position, tool_name: 'diamond_axe' })).error.code, 'WRONG_TOOL');
  assert.equal(f.blocks.get(block.position.toString()).name, 'stone');
});
test('自动挖掘优先使用合适工具；没有工具且空手不可采集时明确拒绝', async () => {
  const f = fixture(), position = { x: 1, y: 64, z: 0 };
  f.setBlock('oak_log', undefined, { canHarvest: () => true, digTime: (type) => type === 5 ? 1 : 10 });
  const axe = f.addItem('iron_axe', 1, { type: 5, maxDurability: 250, durabilityUsed: 0 });
  assert.equal((await f.run('mine_block', { block_name: 'oak_log', position })).data.status, 'completed');
  assert.equal(f.bot.heldItem, axe);

  const g = fixture();
  g.setBlock('stone', undefined, { canHarvest: (type) => type === 5 });
  assert.equal((await g.run('mine_block', { block_name: 'stone', position })).error.code, 'TOOL_MISSING');
  assert.match((await g.run('mine_block', { block_name: 'stone', position })).error.message, /空手无法采集/);
});
test('方块有限查找校验名称、三维半径、返回上限', async () => {
  const f = fixture(); let options;
  f.bot.findBlocks = (o) => { options = o; return [new Vec3(1, 64, 0), new Vec3(2, 64, 0), new Vec3(1, 80, 0)]; };
  const result = await f.run('find_block', { block_name: 'stone', radius: 5, max_results: 1 });
  assert.equal(options.count, 2); assert.equal(options.maxDistance, 5);
  assert.equal(result.data.blocks.length, 1); assert.equal(result.data.truncated, true);
  assert.equal(result.data.blocks[0].visible, true); assert.equal(result.data.blocks[0].diggable, true);
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

test('指定实体名称不匹配时报告实际物品，不假装范围内没有掉落物', async () => {
  const f = fixture();
  f.bot.entities[2] = { id: 2, isValid: true, position: new Vec3(1, 64, 0), getDroppedItem: () => ({ name: 'stone', count: 1 }) };
  const result = await f.run('collect_item', { item_name: 'cobblestone', entity_id: 2 });
  assert.equal(result.error.code, 'ITEM_MISMATCH');
  assert.match(result.error.message, /stone/);
});

test('脚边下方的方块会被拒绝挖掘，避免制造无法拾取的坑', async () => {
  const f = fixture();
  f.setBlock('stone', new Vec3(1, 63, 0));
  const result = await f.run('mine_block', { block_name: 'stone', position: { x: 1, y: 63, z: 0 } });
  assert.equal(result.error.code, 'UNSAFE_TARGET');
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

test('满饥饿允许金苹果及附魔金苹果，普通食物保持拒绝并验证真实消耗', async () => {
  const f = fixture();
  for (const name of ['golden_apple', 'enchanted_golden_apple', 'apple']) f.addItem(name, 2);
  f.bot.registry.foodsByName = { golden_apple: {}, enchanted_golden_apple: {}, apple: {} };
  f.bot.food = 20;
  f.bot.deactivateItem = () => {};
  let consumed = 0;
  f.bot.consume = async () => { consumed++; f.bot.heldItem.count--; };
  for (const name of ['golden_apple', 'enchanted_golden_apple']) {
    const result = await f.run('consume_item', { item_name: name });
    assert.equal(result.data.status, 'completed');
    assert.equal(result.data.after.count, result.data.before.count - 1);
  }
  assert.equal((await f.run('consume_item', { item_name: 'apple' })).error.code, 'CANNOT_CONSUME');
  assert.equal(consumed, 2);
  f.bot.consume = async () => {};
  assert.equal((await f.run('consume_item', { item_name: 'golden_apple' })).data.status, 'uncertain');
});
test('任意物品可丢弃，校验数量、物品存在性和实际增减', async () => {
  const f = fixture(); f.addItem('dirt', 5); f.addItem('diamond');
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: 17 })).error.code, 'INVALID_ARGUMENT');
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: 0 })).error.code, 'INVALID_ARGUMENT');
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: -1 })).error.code, 'INVALID_ARGUMENT');
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: 1.5 })).error.code, 'INVALID_ARGUMENT');
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: '1' })).error.code, 'INVALID_ARGUMENT');
  assert.equal((await f.run('drop_item', { item_name: 'unknown_item', count: 1 })).error.code, 'ITEM_MISSING');
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: 6 })).error.code, 'INSUFFICIENT_ITEMS');
  f.bot.toss = async (type, metadata, count) => { f.inventory.find((item) => item.type === type).count -= count; };
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: 2 })).data.dropped, 2);
  assert.equal((await f.run('drop_item', { item_name: 'diamond', count: 1 })).data.dropped, 1);
  assert.equal((await f.run('drop_item', { item_name: 'dirt', count: 3 })).data.dropped, 3);
  assert.deepEqual(f.bot.inventory.items(), []);
});

test('明确丢弃橡木木板或最后一件附魔装备均可执行，结果未确认时不报完成', async () => {
  const f = fixture(), planks = f.addItem('oak_planks', 8);
  f.addItem('diamond', 1);
  f.bot.toss = async (type, metadata, count) => { f.inventory.find((item) => item.type === type).count -= count; };
  assert.equal((await f.run('drop_item', { item_name: 'oak_planks', count: 4 })).data.dropped, 4);
  assert.equal(planks.count, 4);
  assert.equal((await f.run('drop_item', { item_name: 'oak_planks', count: 17 })).error.code, 'INVALID_ARGUMENT');
  assert.equal((await f.run('drop_item', { item_name: 'diamond', count: 1 })).data.status, 'completed');
  assert.equal((await f.run('drop_item', { item_name: 'oak_planks', count: 4 })).data.status, 'completed');
  const sword = f.addItem('diamond_sword', 1, { enchants: [{ name: 'sharpness', lvl: 5 }] });
  assert.equal((await f.run('drop_item', { item_name: 'diamond_sword', count: 1 })).data.status, 'completed');
  assert.equal(sword.count, 0);
  assert.deepEqual(f.bot.inventory.items(), []);
  f.addItem('enchanted_golden_apple', 2);
  f.bot.toss = async () => {};
  assert.equal((await f.run('drop_item', { item_name: 'enchanted_golden_apple', count: 1 })).data.status, 'uncertain');
});

function navigationFixture() {
  const movement = new EventEmitter();
  let current = { type: 'idle', taskId: null }, stopped = 0;
  movement.getState = () => current;
  movement.goTo = () => { current = { type: 'goto', taskId: 42 }; return { ok: true, taskId: 42, status: 'goto' }; };
  movement.stop = () => { stopped++; current = { type: 'idle', taskId: null }; return { ok: true }; };
  const f = fixture({ movement });
  return { ...f, stopped: () => stopped };
}

test('工具导航等待当前任务实际到达，忽略旧任务结果并移除监听器', async () => {
  const f = navigationFixture();
  const pending = f.run('goto_position', { x: 1, y: 64, z: 0 });
  f.movement.emit('result', { ok: true, status: 'arrived', taskId: 41 });
  await new Promise(setImmediate);
  assert.equal(f.tools.getState().active, 'goto_position');
  f.movement.emit('result', { ok: true, status: 'arrived', taskId: 42 });
  assert.equal((await pending).data.status, 'completed');
  assert.equal(f.movement.listenerCount('result'), 0);
  assert.equal(f.tools.getState().active, null);
  assert.equal(f.stopped(), 0);
});

test('工具导航等待有界，未到达返回 pending 而非误报完成，可按请求 ID 取消', async () => {
  const f = navigationFixture();
  const result = await f.tools.execute('goto_position', { x: 1, y: 64, z: 0, wait_seconds: 0.01 }, 'Steve', 'nav-pending');
  assert.equal(result.data.status, 'pending');
  assert.equal(f.movement.listenerCount('result'), 0);
  f.tools.cancel('Steve', false, 'nav-pending');
  assert.equal(f.stopped(), 1);
});

test('等待导航时路径失败、停止和断线均终止等待，不泄漏监听或重启动作', async () => {
  for (const reason of ['no_path', 'stop', 'end']) {
    const f = navigationFixture();
    const pending = f.run('goto_position', { x: 1, y: 64, z: 0 });
    if (reason === 'no_path') f.movement.emit('result', { ok: false, status: 'no_path', taskId: 42, message: '未找到路径。' });
    else if (reason === 'end') f.bot.emit('end');
    else await f.run('stop_movement', {});
    assert.equal((await pending).error.code, reason === 'no_path' ? 'NAVIGATION_FAILED' : 'CANCELLED');
    await new Promise(setImmediate);
    assert.equal(f.movement.listenerCount('result'), 0);
    assert.equal(f.tools.getState().active, null);
  }
});

test('导航取消时即使状态读取抛异常，也终止等待并释放互斥锁', async () => {
  const f = navigationFixture();
  f.movement.getState = () => { throw new Error('模拟状态读取异常'); };
  const pending = f.run('goto_position', { x: 1, y: 64, z: 0 });
  f.tools.cancel('Steve');
  assert.equal((await pending).error.code, 'CANCELLED');
  await new Promise(setImmediate);
  assert.equal(f.tools.getState().active, null);
  assert.equal(f.movement.listenerCount('result'), 0);
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
test('旧熔炼接口支持64件投料、燃料和取出；超过槽容量在写入前拒绝', async () => {
  const f = fixture(), position = { x: 1, y: 64, z: 0 };
  const raw = f.addItem('raw_iron', 64), coal = f.addItem('coal', 64), product = f.addItem('iron_ingot', 0);
  const slots = [null, null, null], writes = [];
  const win = { fuel: 0, progress: 0, inputItem: () => slots[0], fuelItem: () => slots[1], outputItem: () => slots[2],
    putInput: async (_, __, n) => { writes.push(['input', n]); raw.count -= n; slots[0] = { name: 'raw_iron', count: n }; },
    putFuel: async (_, __, n) => { writes.push(['fuel', n]); coal.count -= n; slots[1] = { name: 'coal', count: n }; },
    takeOutput: async () => { product.count += slots[2].count; slots[2] = null; }
  };
  windowFixture(f, 'furnace', win);
  assert.equal((await f.run('smelt_item', { position, action: 'start', item_name: 'raw_iron', fuel_name: 'coal', count: 65 })).success, false);
  assert.equal((await f.run('smelt_item', { position, action: 'start', item_name: 'raw_iron', fuel_name: 'coal', count: 64, fuel_count: 65 })).success, false);
  f.addItem('bamboo', 256);
  assert.equal((await f.run('smelt_item', { position, action: 'start', item_name: 'raw_iron', fuel_name: 'bamboo', count: 64 })).error.code, 'FURNACE_FUEL_FULL');
  assert.deepEqual(writes, []);
  const start = await f.run('smelt_item', { position, action: 'start', item_name: 'raw_iron', fuel_name: 'coal', count: 64, fuel_count: 64 });
  assert.equal(start.data.status, 'pending');
  assert.deepEqual(writes, [['input', 64], ['fuel', 64]]);
  slots[0] = null; slots[2] = { name: 'iron_ingot', count: 64 };
  assert.equal((await f.run('smelt_item', { position, action: 'status', item_name: 'iron_ingot', count: 64 })).data.outputReady, true);
  assert.equal((await f.run('smelt_item', { position, action: 'take', item_name: 'iron_ingot', count: 64 })).data.taken, 64);
  assert.equal(raw.count, 0); assert.equal(product.count, 64);
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
