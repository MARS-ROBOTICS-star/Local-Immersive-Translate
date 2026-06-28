export const MENU_IDS = {
  itemTranslate: "zotero-itemmenu-babeldoc-translate",
  viewSeparator: "zotero-menuview-babeldoc-translate-separator",
  viewTasks: "zotero-menuview-babeldoc-translate-menuitem",
} as const;

export function removeMenuElement(doc: Document, id: string): void {
  doc.getElementById(id)?.remove();
}

export function removeKnownMenuElements(doc: Document): void {
  Object.values(MENU_IDS).forEach((id) => removeMenuElement(doc, id));
}
