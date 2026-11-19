const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createToolSystem } = require('../src/tools');

function fixture(registry = require('minecraft-data')('1.21.4')) {
  const bot = new EventEmitter();
  bot.entity = { position: { x: 0, y: 64, z: 0 } }; bot.isAlive = true; bot.registry = registry;
  bot.blockAt = bot.findBlocks = () => { throw new Error('read-only source lookup must not scan the world'); };
  const tools = createToolSystem(bot, { movement: {}, logger: { log() {}, error() {} } });
  bot.emit('spawn');
  return { bot, tools, run: (args) => tools.execute('inspect_mining_sources', args, 'Steve') };
}

test('采集来源使用真实版本 drops：石头到圆石、铁矿到粗铁，不修改注册表或世界', async () => {
  const f = fixture();
  const before = JSON.stringify(f.bot.registry.blocksByName);
  const stone = await f.run({ item_name: 'cobblestone', block_name: 'stone' });
  assert.equal(stone.success, true);
  assert.deepEqual(stone.data.sources, [{ name: 'stone' }]);
  const iron = await f.run({ item_name: 'raw_iron' });
  assert.ok(iron.data.sources.some((entry) => entry.name === 'iron_ore'));
  assert.ok(iron.data.sources.some((entry) => entry.name === 'deepslate_iron_ore'));
  assert.equal(iron.data.lootRulesVerified, false);
  assert.equal(JSON.stringify(f.bot.registry.blocksByName), before);
  f.tools.dispose();
});

test('采集来源支持模组数字及对象掉落格式，限制数量并标记截断', async () => {
  const f = fixture({ itemsByName: { 'mod:ore_piece': { id: 10 } }, blocksByName: {
    'mod:ore_a': { drops: [10] }, 'mod:ore_b': { drops: [{ drop: 10 }] },
    'mod:ore_c': { drops: [{ drop: { id: 10, metadata: 0 } }] },
    'mod:unknown': {}, 'mod:empty': { drops: [] }
  } });
  const result = await f.run({ item_name: 'mod:ore_piece', max_results: 2 });
  assert.equal(result.data.sources.length, 2);
  assert.equal(result.data.truncated, true);
  assert.deepEqual((await f.run({ item_name: 'mod:ore_piece', block_name: 'mod:ore_c' })).data.sources, [{ name: 'mod:ore_c' }]);
  assert.equal((await f.run({ item_name: 'mod:ore_piece', block_name: 'mod:unknown' })).data.available, false);
  f.tools.dispose();
});

test('错误来源不伪造掉落，拒绝未知资源与非法参数', async () => {
  const f = fixture();
  assert.deepEqual((await f.run({ item_name: 'diamond', block_name: 'dirt' })).data.sources, []);
  assert.equal((await f.run({ item_name: 'invalid' })).error.code, 'UNKNOWN_ITEM');
  assert.equal((await f.run({ item_name: 'diamond', block_name: 'invalid' })).error.code, 'UNKNOWN_BLOCK');
  assert.equal((await f.run({ item_name: 'diamond', max_results: 17 })).error.code, 'INVALID_ARGUMENT');
  assert.equal((await f.run({ item_name: 'diamond', max_results: '2' })).error.code, 'INVALID_ARGUMENT');
  f.tools.dispose();
});
