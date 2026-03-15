const { app, BrowserWindow, globalShortcut, screen, ipcMain, shell } = require('electron')
const { spawn } = require('child_process')
const path = require('path')

const isDev = process.env.NODE_ENV === 'development'

let sidePanel = null
let palette = null
let backendProcess = null

// ─── Backend ────────────────────────────────────────────────────────────────

function startBackend() {
  const projectRoot = path.join(__dirname, '../../')
  console.log('[electron] Starting backend at', projectRoot)

  backendProcess = spawn('python3', ['-m', 'uvicorn', 'server:app', '--port', '8000'], {
    cwd: projectRoot,
    stdio: 'pipe',
  })

  backendProcess.stdout.on('data', d => process.stdout.write('[backend] ' + d))
  backendProcess.stderr.on('data', d => process.stderr.write('[backend] ' + d))
  backendProcess.on('exit', code => console.log('[backend] exited with', code))
}

// ─── Side Panel ─────────────────────────────────────────────────────────────

function createSidePanel() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize

  sidePanel = new BrowserWindow({
    width: 420,
    height: height,
    x: width - 420,
    y: 0,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    vibrancy: 'ultra-dark',
    visualEffectState: 'active',
    resizable: false,
    hasShadow: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  sidePanel.loadURL(
    isDev ? 'http://localhost:5173' : `file://${path.join(__dirname, '../dist/index.html')}`
  )

  if (isDev) sidePanel.webContents.openDevTools({ mode: 'detach' })
}

// ─── Command Palette ─────────────────────────────────────────────────────────

function createPalette() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize

  palette = new BrowserWindow({
    width: 660,
    height: 72,
    x: Math.round((width - 660) / 2),
    y: Math.round(height * 0.28),
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    vibrancy: 'ultra-dark',
    visualEffectState: 'active',
    resizable: false,
    show: false,
    hasShadow: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  palette.loadURL(
    isDev
      ? 'http://localhost:5173/palette.html'
      : `file://${path.join(__dirname, '../dist/palette.html')}`
  )

  // Hide when it loses focus
  palette.on('blur', () => {
    if (palette && !palette.isDestroyed()) palette.hide()
  })
}

// ─── App lifecycle ───────────────────────────────────────────────────────────

app.whenReady().then(() => {
  startBackend()
  createSidePanel()
  createPalette()

  // Cmd+Shift+A — toggle command palette
  globalShortcut.register('CommandOrControl+Shift+A', () => {
    if (!palette || palette.isDestroyed()) return
    if (palette.isVisible()) {
      palette.hide()
    } else {
      palette.show()
      palette.focus()
      palette.webContents.send('palette-opened')
    }
  })

  // Cmd+Shift+S — toggle side panel
  globalShortcut.register('CommandOrControl+Shift+S', () => {
    if (!sidePanel || sidePanel.isDestroyed()) return
    if (sidePanel.isVisible()) {
      sidePanel.hide()
    } else {
      sidePanel.show()
    }
  })
})

// ─── IPC ─────────────────────────────────────────────────────────────────────

ipcMain.on('hide-palette', () => {
  if (palette && !palette.isDestroyed()) palette.hide()
})

ipcMain.on('resize-palette', (_, height) => {
  if (palette && !palette.isDestroyed()) {
    palette.setSize(660, Math.max(72, Math.min(height, 400)))
  }
})

ipcMain.on('show-side-panel', () => {
  if (sidePanel && !sidePanel.isDestroyed()) {
    sidePanel.show()
    sidePanel.focus()
  }
})

// ─── Cleanup ─────────────────────────────────────────────────────────────────

app.on('will-quit', () => {
  globalShortcut.unregisterAll()
  if (backendProcess) {
    backendProcess.kill()
    backendProcess = null
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
