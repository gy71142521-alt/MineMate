const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createToolSystem } = require('../src/tools');

function fixture() {
  const bot = new EventEmitter();
  bot.entity = { position: { x: 0, y: 64, z: 0 } };
  bot.isAlive = true;
  bot.registry = {
    itemsByName: {
      iron_helmet: { name: 'iron_helmet', displayName: 'Iron Helmet', id: 1, stackSize: 1 },
      iron_ingot: { name: 'iron_ingot', displayName: 'Iron Ingot', id: 2, stackSize: 64 },
      'example:modded_iron_helmet': { name: 'example:modded_iron_helmet', displayName: 'Modded Iron Helmet', id: 3, stackSize: 1 }
    },
    blocksByName: {
      iron_ore: { name: 'iron_ore', displayName: 'Iron Ore', id: 4 },
      deepslate_iron_ore: { name: 'deepslate_iron_ore', displayName: 'Deepslate Iron Ore', id: 5 }
    },
    entitiesByName: {
      cow: { name: 'cow', displayName: 'Cow', id: 6 },
      'example:moon_cow': { name: 'example:moon_cow', displayName: 'Moon Cow', id: 7 }
    }
  };
  const tools = createToolSystem(bot, { movement: {}, logger: { log() {}, error() {} } });
  bot.emit('spawn');
  return { bot, tools };
}

test('resolve_resource reads runtime item registry and matches canonical names', async () => {
  const { tools } = fixture();
  const result = await tools.execute('resolve_resource', { query: 'iron helmet', kind: 'item' }, 'Steve');
  assert.equal(result.success, true);
  assert.equal(result.data.candidates[0].name, 'iron_helmet');
  assert.equal(result.data.ambiguous, false);
});

test('resolve_resource reports ambiguity and includes modded registry entries', async () => {
  const { tools } = fixture();
  const result = await tools.execute('resolve_resource', { query: 'iron', kind: 'any' }, 'Steve');
  assert.equal(result.success, true);
  assert.equal(result.data.ambiguous, true);
  assert.ok(result.data.candidates.some((candidate) => candidate.name === 'example:modded_iron_helmet'));
});

test('resolve_resource is read-only and rejects unsupported kinds', async () => {
  const { bot, tools } = fixture();
  const before = JSON.stringify(bot.registry);
  const result = await tools.execute('resolve_resource', { query: 'iron', kind: 'unknown' }, 'Steve');
  assert.equal(result.success, false);
  assert.equal(result.error.code, 'INVALID_ARGUMENT');
  assert.equal(JSON.stringify(bot.registry), before);
});

test('resolve_resource supports runtime entity types without changing any results', async () => {
  const { bot, tools } = fixture();
  const before = JSON.stringify(bot.registry);
  const entity = await tools.execute('resolve_resource', { query: 'cow', kind: 'entity' }, 'Steve');
  assert.equal(entity.success, true);
  assert.equal(entity.data.candidates[0].name, 'cow');
  assert.equal(entity.data.candidates[0].kind, 'entity');
  assert.equal(entity.data.ambiguous, false);
  const modded = await tools.execute('resolve_resource', { query: 'example:moon_cow', kind: 'entity' }, 'Steve');
  assert.equal(modded.data.candidates[0].name, 'example:moon_cow');
  const all = await tools.execute('resolve_resource', { query: 'cow', kind: 'any' }, 'Steve');
  assert.deepEqual(all.data.candidates, []);
  assert.equal(JSON.stringify(bot.registry), before);
});
