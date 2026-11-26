const { object, number, describe } = require('../schema');
const { ensure, state } = require('../common');

module.exports = function registerMovementQueries(registry) {
  registry.register({ name: 'get_movement_task', description: '按 task_id 查询原移动任务，不启动新导航。arrived 才是到达，cancelled/失败不可视为成功。记录最多保留 128 项。', mutates: false,
    parameters: object({
      task_id: describe(number(1, Number.MAX_SAFE_INTEGER, true), 'goto_position 或 follow 返回的移动任务 ID；不能猜。'),
    }),
    execute({ movement }, a) {
      const task = movement.getTask(a.task_id);
      ensure(task, 'TASK_NOT_FOUND', '移动任务记录不存在或已过期。');
      return state(task.status === 'running' ? 'pending' : task.status === 'arrived' ? 'completed' : 'failed', { movement: task });
    }
  });
};
