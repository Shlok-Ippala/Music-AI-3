interface Window {
  electron?: {
    isElectron: boolean
    hidePalette: () => void
    resizePalette: (height: number) => void
    showSidePanel: () => void
    onPaletteOpened: (cb: () => void) => void
  }
}
