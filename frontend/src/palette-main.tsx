import React from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import CommandPalette from './CommandPalette'

ReactDOM.createRoot(document.getElementById('palette-root')!).render(
  <React.StrictMode>
    <CommandPalette />
  </React.StrictMode>
)
