import { config } from "../package.json";
import {
  DialogHelper,
  VirtualizedTableHelper,
  LargePrefHelper,
} from "zotero-plugin-toolkit";
import {} from "zotero-plugin-toolkit";
import hooks from "./hooks";
import { createZToolkit } from "./utils/ztoolkit";
import api from "./api";
import type { TranslationTaskData } from "./types";

class Addon {
  public data: {
    alive: boolean;
    config: typeof config;
    // Env type, see build.js
    env: "development" | "production";
    ztoolkit: ZToolkit;
    locale?: {
      current: any;
    };
    prefs?: {
      window: Window;
    };
    dialog?: DialogHelper;
    localBackendProcess?: any;
    task: {
      data?: LargePrefHelper;
      window?: Window;
      tableHelper?: VirtualizedTableHelper;
      translationGlobalQueue: TranslationTaskData[];
      translationTaskList: TranslationTaskData[];
      isQueueProcessing: boolean;
    };
  };
  // Lifecycle hooks
  public hooks: typeof hooks;
  // APIs
  public api: typeof api;

  constructor() {
    this.data = {
      alive: true,
      config,
      env: __env__,
      ztoolkit: createZToolkit(),
      task: {
        window: undefined,
        tableHelper: undefined,
        translationGlobalQueue: [],
        translationTaskList: [],
        isQueueProcessing: false,
      },
    };
    this.hooks = hooks;
    this.api = api;
  }
}

export default Addon;
