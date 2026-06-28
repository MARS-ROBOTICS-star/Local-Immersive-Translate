export function registerNotifier(types: _ZoteroTypes.Notifier.Type[]) {
  const callback = {
    notify: async (
      event: string,
      type: string,
      ids: number[] | string[],
      extraData: { [key: string]: any },
    ) => {
      if (!addon?.data.alive) {
        unregisterNotifier(notifierID);
        return;
      }
      addon.hooks.onNotify(event, type, ids, extraData);
    },
  };

  // Register the callback in Zotero as an item observer
  const notifierID = Zotero.Notifier.registerObserver(callback, types);

  Zotero.Plugins.addObserver({
    shutdown: ({ id }) => {
      if (id === addon.data.config.addonID) unregisterNotifier(notifierID);
    },
  });
}

function unregisterNotifier(notifierID: string) {
  Zotero.Notifier.unregisterObserver(notifierID);
}
