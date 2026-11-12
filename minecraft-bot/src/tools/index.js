const { createRegistry } = require('./registry');
const { object, number } = require('./schema');
const { ensure, state } = require('./common');

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
  registry.register({ name: 'follow_player', description: '持续跟随发出请求的玩家，直到停止或目标丢失。', parameters: object(), movement: true,
    execute: ({ movement, username }) => movementResult(movement.follow(username)) });
  registry.register({ name: 'goto_position', description: '启动坐标导航，pending 表示尚未到达。', parameters: object({ x: number(-29999984, 29999984), y: number(-2048, 2047), z: number(-29999984, 29999984) }), movement: true,
    execute: ({ movement, username }, a) => movementResult(movement.goTo(username, a.x, a.y, a.z)) });
  registry.register({ name: 'stop_movement', description: '最高优先级：取消正在执行的工具并停止移动。', parameters: object(), priority: true,
    execute: ({ movement, username }) => movementResult(movement.stop(username), false) });
  // 各功能模块仅注册定义；所有实例共享同一 Bot 与调度器。
  const modules = [require('./mining'), require('./building'), require('./inventory/collect'), require('./combat'), require('./inventory'), require('./interaction').registerInteraction, require('./interaction/transfer'), require('./crafting')];
  for (const register of [...modules, ...(options.modules ?? [])]) register(registry);
  return registry;
}
module.exports = { createToolSystem };
