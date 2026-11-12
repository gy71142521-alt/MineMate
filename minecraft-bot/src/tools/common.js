const { Vec3 } = require('vec3');
class ToolError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
function ensure(condition, code, message) { if (!condition) throw new ToolError(code, message); }
const plainPosition = (p) => ({ x: p.x, y: p.y, z: p.z });
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
const vector = (p) => new Vec3(p.x, p.y, p.z);
function blockAt(ctx, position, maximum = 4.5) {
  ctx.check();
  const { bot } = ctx;
  const minY = bot.game?.minY;
  const height = bot.game?.height;
  ensure(Number.isFinite(minY) && Number.isFinite(height), 'NOT_READY', '当前维度高度不可用。');
  ensure(position.y >= minY && position.y < minY + height, 'OUT_OF_RANGE', '坐标超出当前维度高度。');
  const center = vector(position).offset(0.5, 0.5, 0.5);
  ensure(distance(bot.entity.position.offset(0, 1.5, 0), center) <= maximum, 'OUT_OF_RANGE', '目标超出交互距离，请先导航靠近。');
  const block = bot.blockAt(vector(position));
  ensure(block, 'CHUNK_UNAVAILABLE', '目标区块尚未加载。');
  return block;
}
// Mineflayer 在关闭功能窗口时才把其背包区域复制回 bot.inventory。
// 窗口打开期间从该窗口的背包区域读取，避免用旧数量验证存取/熔炼。
const items = (bot) => Number.isInteger(bot.currentWindow?.inventoryStart) && Number.isInteger(bot.currentWindow?.inventoryEnd)
  ? bot.currentWindow.slots.slice(bot.currentWindow.inventoryStart, bot.currentWindow.inventoryEnd).filter(Boolean)
  : bot.inventory.items();
const count = (bot, name) => items(bot).reduce((sum, item) => sum + (item.name === name ? item.count : 0), 0);
function itemByName(bot, name) {
  const item = items(bot).find((entry) => entry.name === name);
  ensure(item, 'ITEM_MISSING', '背包中没有指定物品。');
  return item;
}
function usable(item) {
  ensure(!item.maxDurability || (Number.isFinite(item.durabilityUsed) && item.maxDurability - item.durabilityUsed > 1), 'TOOL_DAMAGED', '工具剩余耐久不足或不可确认。');
}
const state = (status, extra = {}) => ({ status, ...extra });
module.exports = { ToolError, ensure, plainPosition, distance, vector, blockAt, items, count, itemByName, usable, state };
