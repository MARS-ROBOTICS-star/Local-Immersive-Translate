import { config } from "../../../package.json";
import { TranslationTaskData } from "../../types";

function initTasks() {
  addon.data.task.data = new ztoolkit.LargePrefObject(
    `${config.prefsPrefix}.taskKeys`,
    `${config.prefsPrefix}.task.`,
    "parser",
  );
}

function clearTasks() {
  const taskKeys = getTaskKeys();
  for (const key of taskKeys) {
    removeTask(key);
  }
}

function getTaskKeys(): string[] {
  return addon.data.task.data?.getKeys() || [];
}

function setTaskKeys(taskKeys: string[]): void {
  addon.data.task.data?.setKeys(taskKeys);
}

function getTaskText(keyName: string): string {
  return addon.data.task.data?.getValue(keyName) || "";
}

function setTask(task: TranslationTaskData): void {
  addon.data.task.data?.setValue(task.attachmentId.toString(), task);
}

function removeTask(keyName: string | undefined): void {
  if (!keyName) {
    return;
  }
  addon.data.task.data?.deleteKey(keyName);
}

export { getTaskKeys, getTaskText, setTask, removeTask, initTasks, clearTasks };
