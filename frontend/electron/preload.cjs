const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electron', {
  isElectron: true,
  hidePalette: () => ipcRenderer.send('hide-palette'),
  resizePalette: (height) => ipcRenderer.send('resize-palette', height),
  showSidePanel: () => ipcRenderer.send('show-side-panel'),
  onPaletteOpened: (cb) => ipcRenderer.on('palette-opened', cb),
})
