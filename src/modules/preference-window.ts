import { config } from "../../package.json";
import { getString } from "../utils/locale";
import { getPref, setPref } from "../utils/prefs";
import { showDialog } from "../utils/dialog";
import {
  getLocalModelConfig,
  readLocalModelConfigs,
  writeLocalModelConfigField,
  type LocalModelConfigField,
} from "../utils/local-model-config";
import {
  ensureLocalBackendRunning,
  normalizeLocalBackendPrefs,
} from "./local-backend";
import { getLanguages, getLanguageName } from "./language";
import {
  translateModes,
  translateModels,
  dualModeOptions,
  fontFamilyOptions,
  ocrWorkaroundOptions,
  layoutModelOptions,
  normalizeTranslateModel,
} from "../config";
import type { Language } from "./language/types";
import {
  DEFAULT_SHORTCUTS,
  formatShortcutForDisplay,
  getShortcutFromKeyboardEvent,
  isBareShiftLetterShortcut,
  normalizeShortcutString,
} from "./shortcuts";

type ShortcutInputConfig = {
  inputId: string;
  resetId: string;
  prefKey: "shortcutTranslate" | "shortcutTaskManager";
  otherPrefKey: "shortcutTranslate" | "shortcutTaskManager";
};

const LOCAL_MODEL_CONFIG_FIELDS: {
  field: LocalModelConfigField;
  label: string;
  type: string;
  placeholder: string;
}[] = [
  {
    field: "base_url",
    label: "pref-local-model-base-url",
    type: "text",
    placeholder: "https://api.example.com/v1",
  },
  {
    field: "model",
    label: "pref-local-model-name",
    type: "text",
    placeholder: "provider-model-name",
  },
  {
    field: "api_key",
    label: "pref-local-model-api-key",
    type: "password",
    placeholder: "sk-...",
  },
];

export function registerPrefs() {
  Zotero.PreferencePanes.register({
    pluginID: addon.data.config.addonID,
    src: rootURI + "content/preferences.xhtml",
    label: getString("prefs-title"),
    image: `chrome://${addon.data.config.addonRef}/content/icons/local-immersive-translate.svg`,
  });
}

export async function registerPrefsScripts(_window: Window) {
  // This function is called when the prefs window is opened
  // See addon/content/preferences.xhtml onpaneload
  if (!addon.data.prefs) {
    addon.data.prefs = {
      window: _window,
    };
  } else {
    addon.data.prefs.window = _window;
  }
  setPref("useLocalBackend", true);
  normalizeLocalBackendPrefs();
  const translateModel = normalizeTranslateModel(getPref("translateModel"));
  if (translateModel !== getPref("translateModel")) {
    setPref("translateModel", translateModel);
  }
  buildPrefsPane();
  bindPrefEvents();
}

function buildPrefsPane() {
  const doc = addon.data.prefs?.window?.document;
  if (!doc) {
    return;
  }

  renderLocalModelConfigFields(doc);

  ztoolkit.UI.replaceElement(
    {
      tag: "menulist",
      id: `${config.addonRef}-target-language`,
      attributes: {
        value: getPref("targetLanguage") as string,
        native: "true",
      },
      styles: {
        maxWidth: "250px",
      },
      children: [
        {
          tag: "menupopup",
          children: getLanguages().map((lang) => {
            const nativeLang = getLanguageName(lang, Zotero.locale as Language);
            return {
              tag: "menuitem",
              attributes: {
                label: nativeLang,
                value: lang,
              },
            };
          }),
        },
      ],
      listeners: [
        {
          type: "command",
          listener: (e: Event) => {
            ztoolkit.log(e);
            setPref("targetLanguage", (e.target as XUL.MenuList).value);
          },
        },
      ],
    },
    doc.querySelector(`#${config.addonRef}-target-language-placeholder`)!,
  );

  ztoolkit.UI.replaceElement(
    {
      tag: "menulist",
      id: `${config.addonRef}-translate-mode`,
      attributes: {
        value: getPref("translateMode") as string,
        native: "true",
      },
      styles: {
        maxWidth: "250px",
      },
      children: [
        {
          tag: "menupopup",
          children: translateModes.map((item) => {
            return {
              tag: "menuitem",
              attributes: {
                label: getString(item.label),
                value: item.value,
              },
            };
          }),
        },
      ],
      listeners: [
        {
          type: "command",
          listener: (e: Event) => {
            ztoolkit.log(e);
            setPref("translateMode", (e.target as XUL.MenuList).value);
          },
        },
      ],
    },
    doc.querySelector(`#${config.addonRef}-translate-mode-placeholder`)!,
  );

  const real_translateModels = translateModels;

  ztoolkit.UI.replaceElement(
    {
      tag: "menulist",
      id: `${config.addonRef}-translate-model`,
      attributes: {
        value: normalizeTranslateModel(getPref("translateModel")),
        native: "true",
      },
      styles: {
        maxWidth: "250px",
      },
      children: [
        {
          tag: "menupopup",
          children: real_translateModels.map((item) => {
            return {
              tag: "menuitem",
              attributes: {
                label: getString(item.label),
                value: item.value,
              },
            };
          }),
        },
      ],
      listeners: [
        {
          type: "command",
          listener: (e: Event) => {
            ztoolkit.log(e);
            setPref("translateModel", (e.target as XUL.MenuList).value);
          },
        },
      ],
    },
    doc.querySelector(`#${config.addonRef}-translate-model-placeholder`)!,
  );

  ztoolkit.UI.replaceElement(
    {
      tag: "menulist",
      id: `${config.addonRef}-enable-ocr-workaround`,
      attributes: {
        value: getPref("ocrWorkaround") as string,
        native: "true",
      },
      styles: {
        maxWidth: "250px",
      },
      children: [
        {
          tag: "menupopup",
          children: ocrWorkaroundOptions.map((item) => {
            return {
              tag: "menuitem",
              attributes: {
                label: getString(item.label),
                value: item.value,
              },
            };
          }),
        },
      ],
      listeners: [
        {
          type: "command",
          listener: (e: Event) => {
            ztoolkit.log(e);
            setPref("ocrWorkaround", (e.target as XUL.MenuList).value);
          },
        },
      ],
    },
    doc.querySelector(`#${config.addonRef}-enable-ocr-workaround-placeholder`)!,
  );

  ztoolkit.UI.replaceElement(
    {
      tag: "menulist",
      id: `${config.addonRef}-font-family`,
      attributes: {
        value: getPref("primaryFontFamily") as string,
        native: "true",
      },
      styles: {
        maxWidth: "250px",
      },
      children: [
        {
          tag: "menupopup",
          children: fontFamilyOptions.map((item) => {
            return {
              tag: "menuitem",
              attributes: {
                label: getString(item.label),
                value: item.value,
              },
            };
          }),
        },
      ],
      listeners: [
        {
          type: "command",
          listener: (e: Event) => {
            ztoolkit.log(e);
            setPref("primaryFontFamily", (e.target as XUL.MenuList).value);
          },
        },
      ],
    },
    doc.querySelector(`#${config.addonRef}-font-family-placeholder`)!,
  );

  ztoolkit.UI.replaceElement(
    {
      tag: "menulist",
      id: `${config.addonRef}-dual-mode`,
      attributes: {
        value: getPref("dualMode") as string,
        native: "true",
      },
      styles: {
        maxWidth: "250px",
      },
      children: [
        {
          tag: "menupopup",
          children: dualModeOptions.map((item) => {
            return {
              tag: "menuitem",
              attributes: {
                label: getString(item.label),
                value: item.value,
              },
            };
          }),
        },
      ],
      listeners: [
        {
          type: "command",
          listener: (e: Event) => {
            ztoolkit.log(e);
            setPref("dualMode", (e.target as XUL.MenuList).value);
          },
        },
      ],
    },
    doc.querySelector(`#${config.addonRef}-dual-mode-placeholder`)!,
  );

  ztoolkit.UI.replaceElement(
    {
      tag: "menulist",
      id: `${config.addonRef}-layout-model`,
      attributes: {
        value: getPref("layoutModel") as string,
        native: "true",
      },
      styles: {
        maxWidth: "250px",
      },
      children: [
        {
          tag: "menupopup",
          children: layoutModelOptions.map((item) => {
            return {
              tag: "menuitem",
              attributes: {
                label: getString(item.label),
                value: item.value,
              },
            };
          }),
        },
      ],
      listeners: [
        {
          type: "command",
          listener: (e: Event) => {
            ztoolkit.log(e);
            setPref("layoutModel", (e.target as XUL.MenuList).value);
          },
        },
      ],
    },
    doc.querySelector(`#${config.addonRef}-layout-model-placeholder`)!,
  );
}

function renderLocalModelConfigFields(doc: Document) {
  const container = doc.getElementById(
    `${config.addonRef}-local-model-configs`,
  );
  if (!container) {
    return;
  }

  const htmlNS = "http://www.w3.org/1999/xhtml";
  const configs = readLocalModelConfigs();
  container.replaceChildren();

  const table = doc.createElementNS(htmlNS, "div");
  table.setAttribute(
    "style",
    [
      "display: grid",
      "grid-template-columns: minmax(120px, 150px) minmax(180px, 1.2fr) minmax(140px, 0.8fr) minmax(180px, 1fr)",
      "gap: 6px 8px",
      "align-items: center",
      "max-width: 780px",
      "margin-top: 8px",
    ].join("; "),
  );

  const headerLabels = [
    "pref-local-model-provider",
    ...LOCAL_MODEL_CONFIG_FIELDS.map((field) => field.label),
  ];

  for (const headerLabel of headerLabels) {
    const header = doc.createElementNS(htmlNS, "span");
    header.textContent = getString(headerLabel);
    header.setAttribute("style", "font-weight: 600");
    table.appendChild(header);
  }

  for (const model of translateModels) {
    const modelLabel = doc.createElementNS(htmlNS, "span");
    modelLabel.textContent = getString(model.label);
    modelLabel.setAttribute("style", "white-space: nowrap");
    table.appendChild(modelLabel);

    for (const field of LOCAL_MODEL_CONFIG_FIELDS) {
      const input = doc.createElementNS(htmlNS, "input") as HTMLInputElement;
      input.type = field.type;
      input.value = configs[model.value]?.[field.field] || "";
      input.placeholder = field.placeholder;
      input.setAttribute(
        "style",
        "width: 100%; min-width: 0; box-sizing: border-box",
      );
      input.addEventListener("change", () => {
        writeLocalModelConfigField(model.value, field.field, input.value);
      });
      table.appendChild(input);
    }
  }

  container.appendChild(table);
}

function bindPrefEvents() {
  bindShortcutPrefEvents();
  let isTestingLocalBackend = false;

  function syncLocalBackendPrefsFromInputs() {
    const doc = addon.data.prefs!.window.document;
    const urlInput = doc.querySelector(
      `#zotero-prefpane-${config.addonRef}-local-backend-url`,
    ) as HTMLInputElement | null;
    const projectDirInput = doc.querySelector(
      `#zotero-prefpane-${config.addonRef}-local-backend-project-dir`,
    ) as HTMLInputElement | null;
    const uvPathInput = doc.querySelector(
      `#zotero-prefpane-${config.addonRef}-local-backend-uv-path`,
    ) as HTMLInputElement | null;

    if (urlInput) {
      setPref("localBackendUrl", urlInput.value.trim());
    }
    if (projectDirInput) {
      setPref("localBackendProjectDir", projectDirInput.value.trim());
    }
    if (uvPathInput) {
      setPref("localBackendUvPath", uvPathInput.value.trim());
    }
  }

  addon.data
    .prefs!.window.document?.querySelector(
      `#zotero-prefpane-${config.addonRef}-local-backend-url`,
    )
    ?.addEventListener("change", (e: Event) => {
      setPref("localBackendUrl", (e.target as HTMLInputElement).value.trim());
    });

  addon.data
    .prefs!.window.document?.querySelector(
      `#zotero-prefpane-${config.addonRef}-local-backend-project-dir`,
    )
    ?.addEventListener("change", (e: Event) => {
      setPref(
        "localBackendProjectDir",
        (e.target as HTMLInputElement).value.trim(),
      );
    });

  addon.data
    .prefs!.window.document?.querySelector(
      `#zotero-prefpane-${config.addonRef}-local-backend-uv-path`,
    )
    ?.addEventListener("change", (e: Event) => {
      setPref(
        "localBackendUvPath",
        (e.target as HTMLInputElement).value.trim(),
      );
    });

  const testButton = addon.data.prefs!.window.document?.querySelector(
    `#zotero-prefpane-${config.addonRef}-test-button`,
  ) as XUL.Button | null;

  const handleTestLocalBackend = async (e: Event) => {
    e.preventDefault();
    if (isTestingLocalBackend) {
      return;
    }
    isTestingLocalBackend = true;
    syncLocalBackendPrefsFromInputs();
    testButton?.setAttribute("disabled", "true");
    const progressWindow = new ztoolkit.ProgressWindow(
      addon.data.config.addonName,
      {
        closeOnClick: true,
        closeTime: -1,
      },
    )
      .createLine({
        text: getString("pref-test-starting"),
        type: "default",
        progress: 15,
      })
      .show();
    try {
      const result = await ensureLocalBackendRunning({ forceStart: true });
      let message = result.message;
      if (result.ok) {
        const selectedModel = normalizeTranslateModel(
          getPref("translateModel"),
        );
        progressWindow.changeLine({
          text: getString("pref-test-model-starting"),
          type: "default",
          progress: 65,
        });
        await addon.api.testLocalModel({
          requestModel: selectedModel,
          targetLanguage: getPref("targetLanguage"),
          modelConfig: getLocalModelConfig(selectedModel),
        });
        message = `${message}\n${getString("pref-test-model-success")}`;
      }
      progressWindow.changeLine({
        text: message,
        type: result.ok ? "success" : "error",
        progress: 100,
      });
      progressWindow.startCloseTimer(3000);
      showDialog({
        title: getString(result.ok ? "pref-test-success" : "pref-test-failed"),
        message,
      });
    } catch (error) {
      ztoolkit.log(error);
      const message = getString("pref-test-autostart-failed", {
        args: { reason: (error as Error)?.message || String(error) },
      });
      progressWindow.changeLine({
        text: message,
        type: "error",
        progress: 100,
      });
      progressWindow.startCloseTimer(5000);
      showDialog({
        title: getString("pref-test-failed"),
        message,
      });
    } finally {
      testButton?.removeAttribute("disabled");
      isTestingLocalBackend = false;
    }
  };

  addon.data
    .prefs!.window.document?.querySelector(
      `#zotero-prefpane-${config.addonRef}-test-button`,
    )
    ?.addEventListener("command", handleTestLocalBackend);

  addon.data
    .prefs!.window.document?.querySelector(
      `#zotero-prefpane-${config.addonRef}-test-button`,
    )
    ?.addEventListener("click", handleTestLocalBackend);
}

function bindShortcutPrefEvents() {
  const doc = addon.data.prefs!.window.document;
  const shortcutInputs: ShortcutInputConfig[] = [
    {
      inputId: `zotero-prefpane-${config.addonRef}-shortcut-translate`,
      resetId: `zotero-prefpane-${config.addonRef}-shortcut-translate-reset`,
      prefKey: "shortcutTranslate",
      otherPrefKey: "shortcutTaskManager",
    },
    {
      inputId: `zotero-prefpane-${config.addonRef}-shortcut-task-manager`,
      resetId: `zotero-prefpane-${config.addonRef}-shortcut-task-manager-reset`,
      prefKey: "shortcutTaskManager",
      otherPrefKey: "shortcutTranslate",
    },
  ];

  shortcutInputs.forEach((shortcutInput) => {
    const input = doc.querySelector<HTMLInputElement>(
      `#${shortcutInput.inputId}`,
    );
    const resetButton = doc.querySelector(`#${shortcutInput.resetId}`);
    if (!input || !resetButton) {
      return;
    }

    input.value = formatShortcutForDisplay(getPref(shortcutInput.prefKey));
    input.placeholder = formatShortcutForDisplay(
      DEFAULT_SHORTCUTS[shortcutInput.prefKey],
    );

    input.addEventListener("keydown", (event) => {
      const keyboardEvent = event as KeyboardEvent;
      if (keyboardEvent.key === "Backspace" || keyboardEvent.key === "Delete") {
        return;
      }
      keyboardEvent.preventDefault();
      const shortcutValue = normalizeShortcutFromInputEvent(keyboardEvent);
      if (shortcutValue) {
        input.value = shortcutValue;
        saveShortcutPref(shortcutInput, input);
      }
    });
    input.addEventListener("change", () =>
      saveShortcutPref(shortcutInput, input),
    );
    input.addEventListener("input", () =>
      saveShortcutPref(shortcutInput, input),
    );
    resetButton.addEventListener("command", () => {
      input.value = formatShortcutForDisplay(
        DEFAULT_SHORTCUTS[shortcutInput.prefKey],
      );
      saveShortcutPref(shortcutInput, input);
    });
  });
}

function normalizeShortcutFromInputEvent(event: KeyboardEvent): string {
  return getShortcutFromKeyboardEvent(event);
}

function saveShortcutPref(
  shortcutInput: ShortcutInputConfig,
  input: HTMLInputElement,
) {
  const normalizedValue = normalizeShortcutString(input.value);
  const otherValue = normalizeShortcutString(
    getPref(shortcutInput.otherPrefKey),
  );
  if (normalizedValue !== "" && normalizedValue === otherValue) {
    input.value = formatShortcutForDisplay(getPref(shortcutInput.prefKey));
    setShortcutFeedback(getString("pref-shortcut-duplicate-error"));
    return;
  }
  if (isBareShiftLetterShortcut(normalizedValue)) {
    input.value = formatShortcutForDisplay(getPref(shortcutInput.prefKey));
    setShortcutFeedback(getString("pref-shortcut-native-conflict-error"));
    return;
  }
  input.value = formatShortcutForDisplay(normalizedValue);
  setPref(shortcutInput.prefKey, normalizedValue);
  setShortcutFeedback(getString("pref-shortcut-conflict-guidance"));
}

function setShortcutFeedback(message: string) {
  const feedback = addon.data.prefs!.window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-shortcut-feedback`,
  );
  if (feedback) {
    feedback.textContent = message;
  }
}
