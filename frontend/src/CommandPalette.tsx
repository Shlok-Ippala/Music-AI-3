import React, { useState, useRef, useEffect } from 'react'
import { Zap, Loader2, ArrowRight } from 'lucide-react'
import { wsService, type WsEvent } from './services/websocket'

type PaletteState = 'idle' | 'thinking' | 'done'

const QUICK_COMMANDS = [
  'What\'s the current tempo?',
  'Add a reverb to all tracks',
  'Create a hi-hat pattern',
  'Summarize my project',
]

export default function CommandPalette() {
  const [val, setVal] = useState('')
  const [state, setState] = useState<PaletteState>('idle')
  const [toolCalls, setToolCalls] = useState<string[]>([])
  const [response, setResponse] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const toolCallsRef = useRef<string[]>([])

  const getHeight = (tc: string[], res: string) => {
    let h = 72
    if (tc.length > 0) h += 16 + tc.length * 28
    if (res) h += 16 + Math.min(80, res.length * 0.4 + 40)
    return Math.min(h, 380)
  }

  useEffect(() => {
    inputRef.current?.focus()

    wsService.connect((event: WsEvent) => {
      if (event.type === 'thinking') {
        setState('thinking')
        setToolCalls([])
        toolCallsRef.current = []
        setResponse('')
        window.electron?.resizePalette(72)
      } else if (event.type === 'tool_call') {
        const newCalls = [...toolCallsRef.current, `${event.name}(${event.args})`]
        toolCallsRef.current = newCalls
        setToolCalls(newCalls)
        window.electron?.resizePalette(getHeight(newCalls, ''))
      } else if (event.type === 'message') {
        setState('done')
        const res = event.content || ''
        setResponse(res)
        window.electron?.resizePalette(getHeight(toolCallsRef.current, res))

        // Auto-dismiss after 4s
        setTimeout(() => {
          window.electron?.hidePalette()
          setState('idle')
          setVal('')
          setToolCalls([])
          toolCallsRef.current = []
          setResponse('')
          window.electron?.resizePalette(72)
        }, 4000)
      } else if (event.type === 'error') {
        setState('done')
        setResponse(`Error: ${event.content}`)
        window.electron?.resizePalette(getHeight(toolCallsRef.current, event.content || ''))
      }
    })

    // Re-focus when palette is re-opened
    window.electron?.onPaletteOpened(() => {
      inputRef.current?.focus()
      inputRef.current?.select()
    })
  }, [])

  const submit = () => {
    const content = val.trim()
    if (!content || state === 'thinking') return
    wsService.send(content)
    setState('thinking')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      window.electron?.hidePalette()
    } else if (e.key === 'Enter') {
      submit()
    }
  }

  const showSuggestions = state === 'idle' && !val

  return (
    <div className="flex flex-col w-full gap-1.5 p-1.5" style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}>

      {/* Input row */}
      <div
        className="flex items-center gap-3 px-4 h-[60px] bg-[#0D0D0F]/95 backdrop-blur-2xl border border-white/10 rounded-2xl shadow-2xl"
        style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
      >
        {state === 'thinking' ? (
          <Loader2 className="w-5 h-5 text-accent-violet animate-spin shrink-0" />
        ) : (
          <Zap className="w-5 h-5 text-accent-violet shrink-0" />
        )}

        <input
          ref={inputRef}
          value={val}
          onChange={e => setVal(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask Aria anything..."
          disabled={state === 'thinking'}
          autoFocus
          className="flex-1 bg-transparent text-white text-[15px] placeholder:text-white/25 outline-none disabled:opacity-50"
        />

        {val.trim() && state !== 'thinking' && (
          <button
            onClick={submit}
            className="w-7 h-7 rounded-lg bg-accent-violet flex items-center justify-center hover:bg-accent-violet/80 active:scale-95 transition-all"
          >
            <ArrowRight className="w-3.5 h-3.5 text-white" />
          </button>
        )}
        {state === 'idle' && !val && (
          <kbd className="text-[10px] text-white/20 font-mono shrink-0">⎋ esc</kbd>
        )}
      </div>

      {/* Quick suggestions */}
      {showSuggestions && (
        <div
          className="bg-[#0D0D0F]/95 backdrop-blur-2xl border border-white/10 rounded-2xl shadow-2xl overflow-hidden"
          style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
        >
          {QUICK_COMMANDS.map((cmd, i) => (
            <button
              key={i}
              onClick={() => { setVal(cmd); inputRef.current?.focus() }}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-white/50 hover:text-white hover:bg-white/5 transition-colors text-left"
            >
              <span className="text-accent-violet/50 text-xs">→</span>
              {cmd}
            </button>
          ))}
        </div>
      )}

      {/* Tool calls */}
      {toolCalls.length > 0 && (
        <div
          className="bg-[#0D0D0F]/95 backdrop-blur-2xl border border-white/10 rounded-2xl px-4 py-3 flex flex-col gap-1.5 shadow-2xl"
          style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
        >
          {toolCalls.map((tc, i) => (
            <div key={i} className="text-xs font-mono text-accent-violet/80 flex items-center gap-2">
              <span className="text-white/20">⚙</span>
              <span className="truncate">{tc}</span>
            </div>
          ))}
        </div>
      )}

      {/* Response */}
      {response && (
        <div
          className="bg-[#0D0D0F]/95 backdrop-blur-2xl border border-white/10 rounded-2xl px-4 py-3 text-sm text-white/80 leading-relaxed shadow-2xl"
          style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
        >
          {response}
        </div>
      )}
    </div>
  )
}
