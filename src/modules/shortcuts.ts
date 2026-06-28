import { getPref } from "../utils/prefs";

type ShortcutAction = "translate" | "showTaskManager";
type ShortcutPrefKey = "shortcutTranslate" | "shortcutTaskManager";
type PluginShortcutBinding = {
  action: ShortcutAction;
  shortcut: string;
};

export const DEFAULT_SHORTCUTS: Record<ShortcutPrefKey, string> = {
  shortcutTranslate: "Mod+Shift+B",
  shortcutTaskManager: "Mod+Shift+H",
};

const ACTION_SHORTCUT_PREFS: Record<ShortcutAction, ShortcutPrefKey> = {
  translate: "shortcutTranslate",
  showTaskManager: "shortcutTaskManager",
};

export function registerShortcuts() {
  ztoolkit.Keyboard.register((ev, data) => {
    if (data.type !== "keydown" || ev.repeat) {
      return;
    }

    if (!getPref("enableShortcuts")) {
      return;
    }

    if (isEditableTarget(ev.target)) {
      return;
    }

    if (isBareShiftLetter(ev)) {
      return;
    }

    const pressedShortcut = getShortcutFromKeyboardEvent(ev);
    if (!pressedShortcut) {
      return;
    }

    const action = getPluginShortcutAction(pressedShortcut);
    if (!action) {
      return;
    }
    addon.hooks.onShortcuts(action);
  });
}

function isBareShiftLetter(ev: KeyboardEvent): boolean {
  return (
    ev.shiftKey &&
    !ev.ctrlKey &&
    !ev.metaKey &&
    !ev.altKey &&
    ev.key.length === 1 &&
    /^[a-z]$/i.test(ev.key)
  );
}

export function isBareShiftLetterShortcut(shortcut: string): boolean {
  return /^Shift\+[A-Z]$/.test(normalizeShortcutString(shortcut));
}

export function normalizeShortcutString(value: string): string {
  const parts = value
    .split("+")
    .map((part) => part.trim())
    .filter(Boolean);
  const modifiers = new Set<string>();
  const keys: string[] = [];

  parts.forEach((part) => {
    const lowerPart = part.toLowerCase();
    // `Mod` is the cross-platform primary modifier (Cmd on macOS,
    // Ctrl elsewhere). Keep the explicit `Control` token separate so users can
    // still bind physical Ctrl on macOS or physical Meta on Windows/Linux.
    if (["mod", "cmd", "command", "meta", "⌘"].includes(lowerPart)) {
      modifiers.add("Mod");
    } else if (["ctrl", "⌃"].includes(lowerPart)) {
      modifiers.add("Mod");
    } else if (lowerPart === "control") {
      modifiers.add("Control");
    } else if (lowerPart === "shift" || lowerPart === "⇧") {
      modifiers.add("Shift");
    } else if (["alt", "option", "opt", "⌥"].includes(lowerPart)) {
      modifiers.add("Alt");
    } else {
      keys.push(normalizeKey(part));
    }
  });

  if (keys.length === 0) {
    return "";
  }

  return [...sortModifiers(modifiers), keys[keys.length - 1]].join("+");
}

export function formatShortcutForDisplay(shortcut: string): string {
  const normalized = normalizeShortcutString(shortcut);
  if (!normalized) {
    return "";
  }
  return normalized.replace("Mod", isMacOS() ? "Cmd" : "Ctrl");
}

function getPluginShortcutAction(
  pressedShortcut: string,
): ShortcutAction | null {
  const binding = getConfiguredPluginShortcutWhitelist().find(
    ({ shortcut }) => shortcut === pressedShortcut,
  );
  return binding?.action ?? null;
}

function getConfiguredPluginShortcutWhitelist(): PluginShortcutBinding[] {
  return (
    Object.entries(ACTION_SHORTCUT_PREFS) as [ShortcutAction, ShortcutPrefKey][]
  )
    .map(([action, prefKey]) => ({
      action,
      shortcut: normalizeShortcutString(getPref(prefKey)),
    }))
    .filter(({ shortcut }) => shortcut !== "");
}

export function getShortcutFromKeyboardEvent(ev: KeyboardEvent): string {
  const modifiers = new Set<string>();
  const macOS = isMacOS();
  if ((macOS && ev.metaKey) || (!macOS && ev.ctrlKey)) {
    modifiers.add("Mod");
  }
  if ((macOS && ev.ctrlKey) || (!macOS && ev.metaKey)) {
    modifiers.add("Control");
  }
  if (ev.altKey) {
    modifiers.add("Alt");
  }
  if (ev.shiftKey) {
    modifiers.add("Shift");
  }

  const key = normalizeKey(ev.key);
  if (!key || ["Control", "Shift", "Alt", "Meta"].includes(key)) {
    return "";
  }
  return [...sortModifiers(modifiers), key].join("+");
}

function normalizeKey(key: string): string {
  if (key.length === 1) {
    return key.toUpperCase();
  }

  const keyMap: Record<string, string> = {
    " ": "Space",
    esc: "Escape",
    return: "Enter",
    arrowup: "ArrowUp",
    arrowdown: "ArrowDown",
    arrowleft: "ArrowLeft",
    arrowright: "ArrowRight",
  };
  const lowerKey = key.toLowerCase();
  return keyMap[lowerKey] || key.charAt(0).toUpperCase() + key.slice(1);
}

function sortModifiers(modifiers: Set<string>): string[] {
  const order = ["Mod", "Control", "Alt", "Shift"];
  return order.filter((modifier) => modifiers.has(modifier));
}

function isMacOS(): boolean {
  if (typeof Zotero !== "undefined" && (Zotero as any).isMac !== undefined) {
    return Boolean((Zotero as any).isMac);
  }
  const platform = (Zotero as any).getMainWindow?.().navigator?.platform || "";
  return /Mac/i.test(platform);
}

function isEditableTarget(target: EventTarget | null): boolean {
  const element = target as Element | null;
  if (!element?.tagName) {
    return false;
  }
  const tagName = element.tagName.toLowerCase();
  return (
    tagName === "input" ||
    tagName === "textarea" ||
    tagName === "select" ||
    element.getAttribute("contenteditable") === "true" ||
    (element as HTMLElement).isContentEditable
  );
}
