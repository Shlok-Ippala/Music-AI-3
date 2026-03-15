import React from 'react';
import { Menu, Settings, Bell, Zap, ZapOff } from 'lucide-react';
import { clsx } from 'clsx';

interface HeaderProps {
  isProMode: boolean;
  setIsProMode: (val: boolean) => void;
  toggleSidebar: () => void;
  bridgeConnected: boolean;
}

export default function Header({ isProMode, setIsProMode, toggleSidebar, bridgeConnected }: HeaderProps) {
  return (
    <header className="h-12 w-full border-b border-white/5 bg-bg-panel/80 backdrop-blur-md flex items-center justify-between px-4 shrink-0 relative z-20">
      <div className="flex items-center gap-4">
        <button
          onClick={toggleSidebar}
          className="p-1.5 hover:bg-white/5 rounded-md text-text-secondary hover:text-white transition-colors"
        >
          <Menu className="w-4 h-4" />
        </button>
        <div className="text-xs font-mono text-text-secondary flex items-center gap-2">
          <span>Projects</span>
          <span className="text-white/20">/</span>
          <span className="text-white font-medium">Untitled Session</span>
        </div>
      </div>

      <div className="flex items-center gap-4">
        {/* Mode Toggle */}
        <div className="flex items-center bg-bg-main border border-white/10 rounded-full p-0.5 text-[11px] font-semibold tracking-wide">
          <button
            onClick={() => setIsProMode(false)}
            className={clsx(
              "px-3 py-1 rounded-full transition-all duration-200 flex items-center gap-1.5",
              !isProMode ? "bg-accent-violet text-white shadow-[0_0_12px_rgba(124,58,237,0.3)]" : "text-text-secondary hover:text-white"
            )}
          >
            <ZapOff className="w-3 h-3" /> Simple
          </button>
          <button
            onClick={() => setIsProMode(true)}
            className={clsx(
              "px-3 py-1 rounded-full transition-all duration-200 flex items-center gap-1.5",
              isProMode ? "bg-accent-violet text-white shadow-[0_0_12px_rgba(124,58,237,0.3)]" : "text-text-secondary hover:text-white"
            )}
          >
            <Zap className="w-3 h-3" /> Pro
          </button>
        </div>

        <div className="flex items-center gap-3 border-l border-white/10 pl-4">
          {/* Bridge status */}
          <div className="flex items-center gap-1.5 text-[11px] font-mono">
            <div className={clsx(
              "w-1.5 h-1.5 rounded-full",
              bridgeConnected ? "bg-green-400 shadow-[0_0_6px_rgba(74,222,128,0.8)] animate-pulse" : "bg-red-500"
            )}></div>
            <span className={bridgeConnected ? "text-green-400" : "text-red-400"}>
              {bridgeConnected ? "Bridge Active" : "Disconnected"}
            </span>
          </div>

          <button className="p-1.5 hover:bg-white/5 rounded-md text-text-secondary hover:text-white transition-colors relative">
            <Bell className="w-4 h-4" />
            <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-accent-cyan rounded-full"></span>
          </button>
          <button className="p-1.5 hover:bg-white/5 rounded-md text-text-secondary hover:text-white transition-colors">
            <Settings className="w-4 h-4" />
          </button>
        </div>
      </div>
    </header>
  );
}
