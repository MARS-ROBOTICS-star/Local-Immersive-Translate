import { UITool } from "zotero-plugin-toolkit";
import { getString } from "../utils/locale";
import { MENU_IDS, removeMenuElement } from "../utils/menu";

export function registerMenu(doc: Document) {
  removeMenuElement(doc, MENU_IDS.itemTranslate);

  const itemMenu = doc.getElementById("zotero-itemmenu");
  if (!itemMenu) {
    return;
  }

  const ui = new UITool();
  const menuIcon = `chrome://${addon.data.config.addonRef}/content/icons/local-immersive-translate-toolbar.svg`;
  // item menuitem with icon
  const menuItem = ui.createElement(doc, "menuitem", {
    id: MENU_IDS.itemTranslate,
    attributes: {
      label: getString("menuitem-translate"),
      image: menuIcon,
      class: "menuitem-iconic",
    },
    listeners: [
      {
        type: "command",
        listener: () => addon.hooks.onTranslate(),
      },
    ],
  });
  itemMenu.append(menuItem);
}

export function registerWindowMenu(doc: Document) {
  removeMenuElement(doc, MENU_IDS.viewSeparator);
  removeMenuElement(doc, MENU_IDS.viewTasks);

  const viewMenu = doc.getElementById("menu_viewPopup");
  if (!viewMenu) {
    return;
  }

  const ui = new UITool();
  const separator = ui.createElement(doc, "menuseparator", {
    id: MENU_IDS.viewSeparator,
  });
  // menu->File menuitem
  const menuItem = ui.createElement(doc, "menuitem", {
    id: MENU_IDS.viewTasks,
    attributes: {
      label: getString("menuView-tasks"),
    },
    listeners: [
      {
        type: "command",
        listener: () => addon.hooks.onViewTranslationTasks(),
      },
    ],
  });
  viewMenu.append(separator, menuItem);
}
