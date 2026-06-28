import { setTask, clearTasks, getTaskKeys, getTaskText } from "./store";
import type { TranslationTaskData } from "../../types";

/**
 * 加载已保存的翻译任务数据
 */
export function loadSavedTranslationData() {
  try {
    // 加载翻译任务列表
    const taskKeys = getTaskKeys();
    const savedTaskList = taskKeys.map((key) => getTaskText(key));
    if (savedTaskList && savedTaskList.length > 0) {
      // 在加载到全局变量前进行去重
      const dedupedTasks = removeDuplicateTasks(savedTaskList);

      // 记录清理信息
      if (savedTaskList.length !== dedupedTasks.length) {
        ztoolkit.log(
          `加载时清理了${savedTaskList.length - dedupedTasks.length}条重复记录，保留${dedupedTasks.length}条唯一记录`,
        );
      }

      // 将去重后的数据赋值给全局变量
      addon.data.task.translationTaskList = dedupedTasks;
      ztoolkit.log(
        "已加载保存的翻译任务列表",
        addon.data.task.translationTaskList,
      );
    }
  } catch (error) {
    ztoolkit.log("加载保存的翻译数据时出错", error);
    // 如果出错，使用空数组初始化
    addon.data.task.translationTaskList = [];
  }
}

/**
 * 从任务列表中移除重复数据
 * @param tasks 待处理的任务列表
 * @returns 去重后的任务列表
 */
function removeDuplicateTasks(tasks: any[]): any[] {
  if (!tasks || tasks.length === 0) {
    return [];
  }

  // 创建一个映射，记录每个attachmentId对应的最新任务索引
  const latestTaskIndices = new Map<number, number>();

  // 从后向前遍历，确保保留最新的记录
  for (let i = tasks.length - 1; i >= 0; i--) {
    const task = tasks[i];
    const attachmentId = task.attachmentId;

    if (!latestTaskIndices.has(attachmentId)) {
      latestTaskIndices.set(attachmentId, i);
    }
  }

  // 根据最新任务索引创建新的任务列表
  return Array.from(latestTaskIndices.values())
    .sort((a, b) => a - b) // 按原顺序排列
    .map((index) => tasks[index]);
}

/**
 * 恢复未完成的翻译任务
 * 从translationTaskList中找出未完成的任务恢复到队列中
 * @returns 恢复的任务数量
 */
export function restoreUnfinishedTasks(): number {
  try {
    // 清空当前队列，避免重复
    addon.data.task.translationGlobalQueue = [];

    // 找出状态不是success或failed的任务
    const unfinishedTasks = addon.data.task.translationTaskList.filter(
      (task: any) => {
        const status = task.status || "";
        return (
          status !== "success" && status !== "failed" && status !== "canceled"
        );
      },
    );

    if (unfinishedTasks.length === 0) {
      ztoolkit.log("没有未完成的翻译任务需要恢复");
      return 0;
    }

    ztoolkit.log(
      `找到${unfinishedTasks.length}个未完成的翻译任务，开始检查是否可恢复`,
    );

    // 检查附件是否仍然存在
    let numRestored = 0;
    for (const task of unfinishedTasks) {
      const attachmentId = task.attachmentId;

      try {
        const attachment = Zotero.Items.get(attachmentId);

        if (!attachment || !attachment.isAttachment()) {
          ztoolkit.log(
            `附件ID ${attachmentId} (${task.attachmentFilename}) 已不存在，跳过恢复`,
          );
          continue;
        }

        const isInQueue = addon.data.task.translationGlobalQueue.find(
          (t: any) => t.attachmentId === attachmentId,
        );
        if (isInQueue) {
          ztoolkit.log("任务已存在于队列中，跳过恢复");
          continue;
        } else {
          addon.data.task.translationGlobalQueue.push(task);
          numRestored++;
        }
      } catch (error) {
        ztoolkit.log(
          `检查附件ID ${attachmentId} 是否存在时出错，跳过恢复`,
          error,
        );
      }
    }
    ztoolkit.log(`已恢复${numRestored}个未完成任务`);
    return numRestored;
  } catch (error) {
    ztoolkit.log("恢复未完成任务时出错", error);
    return 0;
  }
}

/**
 * 保存当前的翻译任务数据
 */
export function saveTranslationData() {
  clearTasks();
  try {
    // 保存翻译任务列表
    if (
      addon.data.task.translationTaskList &&
      addon.data.task.translationTaskList.length > 0
    ) {
      addon.data.task.translationTaskList
        .filter(
          (task: any) =>
            task.status !== "success" &&
            task.status !== "failed" &&
            task.status !== "canceled",
        )
        .forEach((task: TranslationTaskData) => setTask(task));
    }
  } catch (error) {
    ztoolkit.log("保存翻译数据时出错", error);
  }
}
