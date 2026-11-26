const { createRegistry } = require('./registry');
const { object, number, describe } = require('./schema');
const { ensure, state } = require('./common');
const { navigate } = require('./movement/navigation');
const { registerResourceTools } = require('./perception/resources');

function createToolSystem(bot, options = {}) {
  const registry = createRegistry(bot, options);
  const query = (name, description, read) => registry.register({ name, description, parameters: object(), mutates: false, execute: read });
  query('get_world_snapshot', '读取当前世界快照，未知字段明确标记不可用。', ({ perception }) => perception.getWorldSnapshot());
  query('query_status', '读取 Bot 自身生命、饥饿、坐标和维度。', ({ perception }) => perception.getPlayerState());
  query('inspect_inventory', '读取背包与装备内容。', ({ perception }) => perception.getInventory());
  query('nearby_entities', '读取附近实体，距离为三维距离。', ({ perception }) => perception.getNearbyEntities());
  query('nearby_blocks', '读取附近常见资源方块。', ({ perception }) => perception.getNearbyBlocks());
  const movementResult = (result, pending = true) => {
    ensure(result?.ok, 'MOVEMENT_REJECTED', result?.message ?? '移动被拒绝。');
    return state(pending ? 'pending' : 'completed', { movement: result });
  };
  registry.register({ name: 'follow_player', description: '持续跟随发出请求的玩家，直到停止或目标丢失。', parameters: object(), movement: true, compatibleControls: true,
    execute: ({ movement, username }) => movementResult(movement.follow(username)) });
  registry.register({ name: 'goto_position', description: '坐标导航，默认最多等 15 秒；completed 表示满足到达半径，交互前仍须复查距离/视线。pending 尚未到达。arrival_range 可选 0～2，默认 2；0 要求进入指定脚部方块，且该位置与头部必须是空气，用于交互站位。wait_seconds=0 立即返回。不会自动攻击或挖掘。',
    parameters: object({
      x: describe(number(-29999984, 29999984), '目标脚部方块 X 坐标；世界坐标，导航时会向下取整。'),
      y: describe(number(-2048, 2047), '目标脚部方块 Y 坐标；必须当前维度有效。'),
      z: describe(number(-29999984, 29999984), '目标脚部方块 Z 坐标；世界坐标，导航时会向下取整。'),
      wait_seconds: describe(number(0, 15), '本次调用最多等待多少秒（0-15，默认 15）；0 表示立即返回 pending/completed，不等待到达。'),
      arrival_range: describe(number(0, 2), '到达半径（0-2，默认 2）；0 表示必须站进目标脚部方块，且脚部/头部必须为空气，用于精确交互站位。'),
    }, ['x', 'y', 'z']), movement: true, compatibleControls: true,
    execute: navigate });
  require('./movement/tools')(registry);
  require('./movement/queries')(registry);
  require('./movement/standing')(registry);
  registerResourceTools(registry);
  require('./crafting/recipes')(registry);
  registry.register({ name: 'stop_movement', description: '最高优先级：取消正在执行的工具并停止移动。', parameters: object(), priority: true,
    execute: ({ movement, username }) => movementResult(movement.stop(username), false) });
  // 各功能模块仅注册定义；所有实例共享同一 Bot 与调度器。
  const modules = [require('./mining'), require('./building'), require('./inventory/collect'), require('./combat'), require('./inventory'), require('./interaction').registerInteraction, require('./interaction/transfer'), require('./crafting')];
  for (const register of [...modules, ...(options.modules ?? [])]) register(registry);
  return registry;
}
module.exports = { createToolSystem };
