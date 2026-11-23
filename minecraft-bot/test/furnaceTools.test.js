const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { Vec3 } = require('vec3');
const { createToolSystem } = require('../src/tools');
const { createApiServer } = require('../src/httpServer');
const registry = require('prismarine-registry')('1.21.4');
const Item = require('prismarine-item')(registry);
const windows = require('prismarine-windows')(registry);
const position = { x: 1, y: 64, z: 0 };
const logger = { log() {}, error() {} };

function setup(t, options = {}) {
  const bot = new EventEmitter(), window = windows.createWindow(1, 'minecraft:furnace', 'Furnace');
  bot.registry = registry; bot.isAlive = true;
  bot.entity = { id: 1, isValid: true, position: new Vec3(0.5, 64, 0.5) };
  bot.game = { minY: -64, height: 384, dimension: 'overworld' };
  bot.inventory = windows.createWindow(0, 'minecraft:inventory', 'Inventory');
  bot.blockAt = (p) => ({ name: 'furnace', position: p.floored() });
  window.fuel = 0; window.fuelSeconds = 0; window.totalFuelSeconds = 0; window.progress = 0;
  window.inputItem = () => window.slots[0]; window.fuelItem = () => window.slots[1]; window.outputItem = () => window.slots[2];
  let opens = 0, closes = 0;
  const transfers = [];
  const item = (name, count, slot) => { const value = new Item(registry.itemsByName[name].id, count, 0); value.slot = slot; return value; };
  const set = (slot, name, count) => { window.slots[slot] = count ? item(name, count, slot) : null; };
  const add = (name, count, slot = null) => {
    const destination = slot ?? window.slots.findIndex((entry, i) => i >= 3 && !entry);
    assert.ok(destination >= 3); set(destination, name, count); return window.slots[destination];
  };
  const move = (sourceStart, sourceEnd, destStart, destEnd, type, count) => {
    let remaining = count;
    for (let i = sourceStart; i < sourceEnd && remaining; i++) {
      const source = window.slots[i];
      if (!source || source.type !== type) continue;
      for (let j = destStart; j < destEnd && remaining && source.count; j++) {
        const dest = window.slots[j];
        if (dest && !Item.equal(dest, source, false, true)) continue;
        const moved = Math.min(source.count, remaining, source.stackSize - (dest?.count ?? 0));
        if (!moved) continue;
        if (!dest) window.slots[j] = item(source.name, moved, j); else dest.count += moved;
        source.count -= moved; remaining -= moved;
      }
      if (!source.count) window.slots[i] = null;
    }
    assert.equal(remaining, 0, '测试 API 不能凭空添加/取出物品');
  };
  bot.openFurnace = async () => { opens++; bot.currentWindow = window; return window; };
  bot.closeWindow = async (win) => {
    assert.equal(win, window); closes++; bot.currentWindow = null;
    window.emit('close');
    // 与实际 Mineflayer 一样，窗口关闭后才更新独立背包，打开期间刻意保持旧数据。
    bot.inventory.slots.fill(null);
    for (let i = 3; i < 39; i++) if (window.slots[i]) bot.inventory.slots[i + 6] = item(window.slots[i].name, window.slots[i].count, i + 6);
  };
  window.putInput = async (type, meta, count) => {
    transfers.push(['input', type, count]); move(3, 39, 0, 1, type, count);
    if (f.afterPut) await f.afterPut('input');
  };
  window.putFuel = async (type, meta, count) => {
    transfers.push(['fuel', type, count]); move(3, 39, 1, 2, type, count);
    if (f.afterPut) await f.afterPut('fuel');
  };
  window.takeOutput = async () => {
    const product = window.outputItem(); transfers.push(['output', product.count]);
    move(2, 3, 3, 39, product.type, product.count);
    if (f.afterTake) await f.afterTake();
  };
  const tools = createToolSystem(bot, { movement: { stop: () => ({ ok: true }) }, logger, ...options });
  bot.emit('spawn'); t.after(() => tools.dispose());
  const run = (name, args = {}, id) => tools.execute(name, { position, ...args }, 'Steve', id);
  const f = { bot, window, tools, run, set, add, item, transfers, afterPut: null, afterTake: null,
    opens: () => opens, closes: () => closes };
  return f;
}

test('添加原料与补充同类原料是增量操作，读取实际窗口背包而非旧 inventory', async (t) => {
  const f = setup(t); f.add('raw_iron', 10);
  assert.equal(f.bot.inventory.items().length, 0, '打开前的背包快照故意不含新物品');
  let result = await f.run('furnace_add_input', { item_name: 'raw_iron', count: 3 });
  assert.equal(result.data.status, 'completed'); assert.equal(f.window.inputItem().count, 3);
  result = await f.run('furnace_add_input', { item_name: 'raw_iron', count: 2 });
  assert.equal(result.data.inventoryDelta, -2); assert.equal(f.window.inputItem().count, 5);
  assert.equal(result.data.expectedOutput, 'iron_ingot');
  assert.equal(f.bot.currentWindow, null); assert.equal(f.opens(), f.closes());
});

test('原料/产品冲突或输出满栈拒绝追加，不修改其它槽位', async (t) => {
  const f = setup(t); f.add('raw_iron', 5); f.set(0, 'raw_gold', 1);
  assert.equal((await f.run('furnace_add_input', { item_name: 'raw_iron', count: 1 })).error.code, 'FURNACE_INPUT_CONFLICT');
  f.set(0, 'raw_gold', 0); f.set(2, 'gold_ingot', 1);
  assert.equal((await f.run('furnace_add_input', { item_name: 'raw_iron', count: 1 })).error.code, 'FURNACE_OUTPUT_CONFLICT');
  f.set(2, 'iron_ingot', 64);
  assert.equal((await f.run('furnace_add_input', { item_name: 'raw_iron', count: 1 })).error.code, 'FURNACE_OUTPUT_FULL');
  assert.equal(f.transfers.length, 0); assert.equal(f.window.outputItem().count, 64);
});

test('原料数量、堆叠空间、未知物品及不支持配方在修改前拒绝', async (t) => {
  const f = setup(t); f.add('raw_iron', 4); f.set(0, 'raw_iron', 63);
  assert.equal((await f.run('furnace_add_input', { item_name: 'raw_iron', count: 2 })).error.code, 'FURNACE_INPUT_FULL');
  assert.equal((await f.run('furnace_add_input', { item_name: 'raw_iron', count: 5 })).error.code, 'INSUFFICIENT_ITEMS');
  assert.equal((await f.run('furnace_add_input', { item_name: 'unknown_mod_ore', count: 1 })).error.code, 'UNKNOWN_ITEM');
  f.add('diamond', 1);
  assert.equal((await f.run('furnace_add_input', { item_name: 'diamond', count: 1 })).error.code, 'UNSUPPORTED_RECIPE');
  assert.equal(f.transfers.length, 0);
});

test('正在加工时原料被消耗转为产品，投料核验使用总量守恒', async (t) => {
  const f = setup(t); f.add('raw_iron', 4); f.set(0, 'raw_iron', 1); f.set(2, 'iron_ingot', 2);
  f.afterPut = async () => { f.window.slots[0].count--; f.window.slots[2].count++; };
  const result = await f.run('furnace_add_input', { item_name: 'raw_iron', count: 3 });
  assert.equal(result.data.status, 'completed'); assert.equal(result.data.processedDuringTransfer, 1);
});

test('自动燃料优先煤炭，缺煤回退木炭、原木/木板等已知可燃物', async (t) => {
  for (const fallback of ['charcoal', 'oak_log', 'birch_planks', 'spruce_log', 'stick', 'bamboo']) {
    const f = setup(t); f.add(fallback, 3); f.add('coal', 1);
    let result = await f.run('furnace_add_fuel');
    assert.equal(result.data.status, 'completed'); assert.equal(result.data.item, 'coal');
    f.set(1, 'coal', 0);
    result = await f.run('furnace_add_fuel', { count: 2 });
    assert.equal(result.data.status, 'completed'); assert.equal(result.data.item, fallback);
    assert.equal(result.data.requested, 2);
  }
});

test('非空燃料槽只能同类补充；不为了煤炭优先而覆盖其它燃料', async (t) => {
  const f = setup(t); f.add('coal', 5); f.add('charcoal', 4); f.set(1, 'charcoal', 2);
  let result = await f.run('furnace_add_fuel', { count: 2 });
  assert.equal(result.data.item, 'charcoal'); assert.equal(f.window.fuelItem().count, 4);
  result = await f.run('furnace_add_fuel', { fuel_name: 'coal', count: 1 });
  assert.equal(result.error.code, 'FURNACE_FUEL_CONFLICT'); assert.equal(f.window.fuelItem().name, 'charcoal');
});

test('缺燃料、指定燃料不足/不可燃、燃料槽满会清晰失败', async (t) => {
  const f = setup(t);
  assert.equal((await f.run('furnace_add_fuel')).error.code, 'FUEL_MISSING');
  f.add('diamond', 1);
  assert.equal((await f.run('furnace_add_fuel', { fuel_name: 'diamond' })).error.code, 'UNSUPPORTED_FUEL');
  f.add('coal', 2);
  assert.equal((await f.run('furnace_add_fuel', { count: 3 })).error.code, 'INSUFFICIENT_ITEMS');
  f.set(1, 'coal', 64);
  assert.equal((await f.run('furnace_add_fuel')).error.code, 'FURNACE_FUEL_FULL');
  assert.equal(f.transfers.length, 0);
});

test('添加燃料后立即燃烧一件，以背包减少和燃烧秒数增长共同确认', async (t) => {
  const f = setup(t); f.add('coal', 1); f.set(0, 'raw_iron', 1);
  f.afterPut = async () => { f.set(1, 'coal', 0); f.window.fuelSeconds = 80; f.window.totalFuelSeconds = 80; };
  const result = await f.run('furnace_add_fuel');
  assert.equal(result.data.status, 'completed'); assert.equal(result.data.burnObserved, true);
  assert.equal(result.data.slotDelta, 0); assert.equal(result.data.inventoryDelta, -1);
});

test('燃烧属性延迟到达时有限观察；未知属性不能伪造点火成功', async (t) => {
  const f = setup(t); f.add('coal', 1); f.window.fuelSeconds = null;
  const baseline = setTimeout(() => { f.window.fuelSeconds = 0; }, 20); t.after(() => clearTimeout(baseline));
  f.afterPut = async () => { f.set(1, 'coal', 0); setTimeout(() => { f.window.fuelSeconds = 80; }, 20); };
  assert.equal((await f.run('furnace_add_fuel')).data.status, 'completed');
  f.add('coal', 1); f.window.fuelSeconds = null;
  f.afterPut = async () => f.set(1, 'coal', 0);
  const result = await f.run('furnace_add_fuel');
  assert.equal(result.data.status, 'uncertain'); assert.equal(result.data.confirmed, false);
});

test('输出满 64 时独立取出整栈，再补同类原料，不要求加工结束', async (t) => {
  const f = setup(t); f.set(0, 'raw_iron', 2); f.set(1, 'coal', 2); f.set(2, 'iron_ingot', 64); f.add('raw_iron', 5);
  let result = await f.run('furnace_take_output', { item_name: 'iron_ingot' });
  assert.equal(result.data.status, 'completed'); assert.equal(result.data.taken, 64);
  assert.equal(f.window.outputItem(), null);
  result = await f.run('furnace_add_input', { item_name: 'raw_iron', count: 3 });
  assert.equal(result.data.status, 'completed'); assert.equal(f.window.inputItem().count, 5);
  assert.equal(f.window.fuelItem().count, 2);
});

test('可一次取出 64 个产品并检查合并背包容量；不静默少取', async (t) => {
  const f = setup(t); f.set(2, 'iron_ingot', 64); f.add('iron_ingot', 60);
  const result = await f.run('furnace_take_output', { item_name: 'iron_ingot', count: 64 });
  assert.equal(result.data.status, 'completed'); assert.equal(result.data.inventoryDelta, 64);
  assert.equal(f.window.outputItem(), null);
  assert.equal(f.bot.inventory.items().filter(i => i.name === 'iron_ingot').reduce((s, i) => s + i.count, 0), 124);
});

test('产品未就绪或背包容量不足时拒绝，不取走原料和燃料', async (t) => {
  const f = setup(t); f.set(0, 'raw_iron', 3); f.set(1, 'coal', 1); f.set(2, 'iron_ingot', 4);
  for (let slot = 3; slot < 39; slot++) f.add('dirt', 64, slot);
  assert.equal((await f.run('furnace_take_output', { item_name: 'gold_ingot', count: 1 })).error.code, 'OUTPUT_NOT_READY');
  assert.equal((await f.run('furnace_take_output', { item_name: 'iron_ingot', count: 5 })).error.code, 'OUTPUT_COUNT_MISMATCH');
  assert.equal((await f.run('furnace_take_output', { item_name: 'iron_ingot', count: 2 })).error.code, 'OUTPUT_COUNT_MISMATCH');
  assert.equal((await f.run('furnace_take_output', { item_name: 'iron_ingot', count: 4 })).error.code, 'INVENTORY_FULL');
  assert.equal(f.transfers.length, 0); assert.equal(f.window.inputItem().count, 3);
});

test('取出期间继续加工的产物不导致误判，仍必须核验实际背包增量', async (t) => {
  const f = setup(t); f.set(0, 'raw_iron', 3); f.set(2, 'iron_ingot', 10);
  f.afterTake = async () => { f.window.slots[0].count--; f.set(2, 'iron_ingot', 1); };
  const result = await f.run('furnace_take_output', { item_name: 'iron_ingot' });
  assert.equal(result.data.status, 'completed'); assert.equal(result.data.slotDelta, -9);
  assert.equal(result.data.processedDuringTransfer, 1); assert.equal(result.data.inventoryDelta, 10);
});

test('API 无效果或仅背包减少/增加时返回 uncertain，不能虚构双边转移', async (t) => {
  const f = setup(t); f.add('raw_iron', 2);
  f.window.putInput = async () => {};
  assert.equal((await f.run('furnace_add_input', { item_name: 'raw_iron', count: 1 })).data.status, 'uncertain');
  f.set(2, 'iron_ingot', 4); f.window.takeOutput = async () => f.add('iron_ingot', 4);
  assert.equal((await f.run('furnace_take_output', { item_name: 'iron_ingot', count: 4 })).data.status, 'uncertain');
  assert.equal(f.bot.currentWindow, null);
});

test('Schema 严格校验且新工具定义只注册一次，单次数量上限 64', async (t) => {
  const f = setup(t); const names = f.tools.list().map(d => d.name);
  for (const name of ['furnace_add_input', 'furnace_add_fuel', 'furnace_take_output']) {
    assert.equal(names.filter(n => n === name).length, 1);
    for (const count of [0, 65, 1.5, '1', true]) {
      const args = name === 'furnace_add_fuel' ? { count } : { item_name: 'raw_iron', count };
      assert.equal((await f.run(name, args)).error.code, 'INVALID_ARGUMENT');
    }
  }
  assert.equal(f.opens(), 0);
});

test('超时/取消/断线清理窗口，未结算的底层写入继续持锁且不重发', async (t) => {
  for (const reason of ['cancel', 'end', 'timeout']) {
    const f = setup(t, { timeoutMs: reason === 'timeout' ? 20 : 10000 }); f.add('raw_iron', 2);
    let settle, entered;
    const opened = new Promise(r => { entered = r; });
    f.window.putInput = () => { entered(); return new Promise(r => { settle = r; }); };
    const pending = f.run('furnace_add_input', { item_name: 'raw_iron', count: 1 }, reason);
    await opened;
    if (reason === 'cancel') f.tools.cancel('Steve');
    if (reason === 'end') f.bot.emit('end');
    const result = await pending;
    assert.equal(result.error.code, reason === 'timeout' ? 'TIMEOUT' : 'CANCELLED');
    assert.equal(f.bot.currentWindow, null); assert.equal(f.closes(), 1);
    if (reason !== 'end') assert.equal((await f.run('furnace_add_input', { item_name: 'raw_iron', count: 1 })).error.code, 'BUSY');
    settle(); await new Promise(setImmediate);
    assert.equal(f.tools.getState().active, null);
  }
});

test('相同 requestId 不重复投料；窗口异常和已有窗口不会继续操作', async (t) => {
  const f = setup(t); f.add('raw_iron', 4);
  const args = { item_name: 'raw_iron', count: 2 };
  const first = await f.run('furnace_add_input', args, 'once');
  assert.deepEqual(await f.run('furnace_add_input', args, 'once'), first);
  assert.equal(f.window.inputItem().count, 2); assert.equal(f.transfers.length, 1);
  f.bot.currentWindow = f.window;
  assert.equal((await f.run('furnace_add_input', args)).error.code, 'WINDOW_BUSY');
  f.bot.currentWindow = null; f.window.putInput = async () => { throw Error('controlled API failure'); };
  assert.equal((await f.run('furnace_add_input', args)).error.code, 'EXECUTION_FAILED');
  assert.equal(f.bot.currentWindow, null);
});

test('真实 HTTP 注册目录与三种炉槽操作一致，复用同一个 Bot 和现有工具接口', async (t) => {
  const f = setup(t); f.add('raw_iron', 3); f.add('coal', 2); f.set(2, 'iron_ingot', 64);
  f.bot.mineMateStatus = () => ({ connected: true, spawned: true });
  const server = createApiServer({ bot: f.bot, tools: f.tools, logger });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  t.after(async () => { server.closeAllConnections(); await new Promise(r => server.close(r)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const catalog = (await (await fetch(base + '/api/tools')).json()).data;
  assert.ok(catalog.tools.some(d => d.name === 'furnace_add_fuel'));
  for (const [i, tool, args] of [[1, 'furnace_take_output', { item_name: 'iron_ingot', count: 64 }],
    [2, 'furnace_add_input', { item_name: 'raw_iron', count: 3 }], [3, 'furnace_add_fuel', { count: 1 }]]) {
    const response = await fetch(base + '/api/tools/execute', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'Steve', requestId: `furnace-http-${i}`, tool, generation: catalog.generation,
        instanceId: catalog.instanceId, arguments: { position, ...args } }) });
    const body = await response.json(); assert.equal(response.status, 200, JSON.stringify(body));
    assert.equal(body.data.success, true, JSON.stringify(body)); assert.equal(body.data.data.status, 'completed', JSON.stringify(body));
  }
  assert.equal(f.window.inputItem().count, 3); assert.equal(f.window.fuelItem().name, 'coal');
  assert.equal(f.window.outputItem(), null); assert.equal(f.opens(), f.closes());
});

test('实际 Mineflayer 熔炉插件：putInput/putFuel/takeOutput 使用官方签名和窗口清理', async (t) => {
  const f = setup(t); f.add('raw_iron', 3); f.add('coal', 1);
  const original = { open: f.bot.openFurnace, input: f.window.putInput, fuel: f.window.putFuel, take: f.window.takeOutput };
  f.bot._client = new EventEmitter();
  f.bot.openBlock = async () => {
    const win = await original.open();
    setImmediate(() => {
      f.bot._client.emit('craft_progress_bar', { windowId: win.id, property: 1, value: 1600 });
      f.bot._client.emit('craft_progress_bar', { windowId: win.id, property: 0, value: 0 });
    });
    return win;
  };
  f.bot.transfer = async (a) => {
    assert.equal(a.window, f.window);
    assert.deepEqual([a.sourceStart, a.sourceEnd], [3, 39]);
    assert.equal(a.destEnd, a.destStart + 1);
    assert.ok(a.destStart === 0 || a.destStart === 1, '不对只读输出槽调用 transfer');
    return (a.destStart === 0 ? original.input : original.fuel)(a.itemType, a.metadata, a.count);
  };
  f.bot.putAway = async (slot) => { assert.equal(slot, 2); return original.take(); };
  require('mineflayer/lib/plugins/furnace')(f.bot);
  assert.equal((await f.run('furnace_add_input', { item_name: 'raw_iron', count: 3 })).data.status, 'completed');
  assert.equal((await f.run('furnace_add_fuel')).data.status, 'completed');
  f.set(2, 'iron_ingot', 64);
  const result = await f.run('furnace_take_output', { item_name: 'iron_ingot' });
  assert.equal(result.data.status, 'completed'); assert.equal(result.data.taken, 64);
  assert.equal(f.bot._client.listenerCount('craft_progress_bar'), 0);
});
