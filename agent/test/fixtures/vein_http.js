// 真实 Node 工具/HTTP/方块射线，受控移动与库存；不连接 Minecraft。
const { EventEmitter } = require('node:events');
const { Vec3 } = require('vec3');
const { createToolSystem } = require('./src/tools');
const { createApiServer } = require('./src/httpServer');
const { createPerception } = require('./src/tools/perception');
const bot = new EventEmitter();
bot.registry = require('prismarine-registry')('1.21.4');
const World = require('prismarine-world')(bot.registry), Chunk = require('prismarine-chunk')(bot.registry);
bot.world = new World().sync;
for (let x = -1; x <= 1; x++) for (let z = -1; z <= 1; z++) bot.world.setColumn(x, z, new Chunk({ minY: -64, worldHeight: 384 }));
const set = (name, x, y, z) => bot.world.setBlockStateId(new Vec3(x, y, z), bot.registry.blocksByName[name].minStateId);
for (let x = -3; x <= 12; x++) for (let z = -3; z <= 3; z++) set('stone', x, 63, z);
for (let x = -1; x <= 10; x++) {
  for (const z of [-1, 1]) for (let y = 64; y <= 68; y++) set('bedrock', x, y, z);
  set('bedrock', x, 67, 0);
}
set('stone', 2, 64, 0); set('stone', 2, 65, 0);
for (const x of [6, 7]) set('iron_ore', x, 64, 0);
bot.entity = { id: 1, position: new Vec3(0.5, 64, 0.5), eyeHeight: 1.62, isValid: true };
bot.entities = { 1: bot.entity }; bot.isAlive = true; bot.health = 20; bot.food = 20;
bot.game = { minY: -64, height: 384, dimension: 'overworld' };
bot.blockAt = p => bot.world.getBlock(p.floored());
bot.findBlocks = ({ matching, maxDistance, count }) => {
  const out = [];
  for (let x = -3; x <= 12; x++) for (let y = 63; y <= 68; y++) for (let z = -3; z <= 3; z++) {
    const p = new Vec3(x, y, z);
    if (p.distanceTo(bot.entity.position) <= maxDistance && bot.blockAt(p)?.type === matching) out.push(p);
  }
  return out.sort((a, b) => a.distanceTo(bot.entity.position) - b.distanceTo(bot.entity.position)).slice(0, count);
};
const inventory = [{ name: 'iron_pickaxe', type: bot.registry.itemsByName.iron_pickaxe.id, count: 1, stackSize: 1 }];
bot.inventory = { slots: inventory, items: () => inventory, emptySlotCount: () => 30 };
bot.equip = async item => { bot.heldItem = item; }; bot.unequip = async () => { bot.heldItem = null; };
bot.canDigBlock = b => b.diggable && b.position.distanceTo(bot.entity.position) < 5;
bot.canSeeBlock = b => {
  const eye = bot.entity.position.offset(0, 1.62, 0), end = b.position.offset(0.5, 0.5, 0.5);
  return Boolean(bot.world.raycast(eye, end.minus(eye).normalize(), eye.distanceTo(end) + 0.01)?.position.equals(b.position));
};
let dropId = 20;
bot.dig = async b => {
  set('air', b.position.x, b.position.y, b.position.z);
  if (b.name === 'iron_ore') {
    const id = dropId++;
    bot.entities[id] = { id, isValid: true, position: b.position.offset(0.5, 0, 0.5),
      getDroppedItem: () => ({ name: 'raw_iron', count: 1 }) };
  }
};
bot.stopDigging = () => {}; bot.clearControlStates = () => {};
bot.pathfinder = { goal: null, setGoal(goal) {
  this.goal = goal;
  if (goal) for (const e of Object.values(bot.entities)) if (e.getDroppedItem && e.isValid &&
      e.position.distanceTo(bot.entity.position) < 1.5) {
    inventory.push({ name: 'raw_iron', type: bot.registry.itemsByName.raw_iron.id, count: 1, stackSize: 64 });
    e.isValid = false; delete bot.entities[e.id];
  }
} };
let task = null, taskId = 0;
const movement = { stop: () => ({ ok: true }), getState: () => ({ type: 'idle' }),
  goTo: (_who, x, y, z) => { bot.entity.position = new Vec3(x + 0.5, y, z + 0.5);
    task = { taskId: ++taskId, status: 'arrived' }; return { ok: true, taskId }; }, getTask: () => task };
const logger = { log() {}, error() {}, warn() {} };
const perception = createPerception(bot, { movement, logger });
bot.tools = createToolSystem(bot, { movement, perception, logger }); bot.tools.guardMovement(); bot.emit('spawn');
bot.mineMateStatus = () => ({ connected: true, spawned: true });
const server = createApiServer({ bot, movement, perception, logger });
server.listen(0, '127.0.0.1', () => console.log(server.address().port));
