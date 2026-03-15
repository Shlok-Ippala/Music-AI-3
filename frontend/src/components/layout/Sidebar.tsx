import React from 'react';
import { Home, FolderKanban, Library, Sliders, Activity, Settings, User } from 'lucide-react';
import { clsx } from 'clsx';

const NAV_ITEMS = [
  { icon: Home, label: 'Dashboard' },
  { icon: FolderKanban, label: 'Projects', active: true },
  { icon: Library, label: 'Samples' },
  { icon: Sliders, label: 'Mixer' },
  { icon: Activity, label: 'Automation' },
  { icon: Settings, label: 'Settings' },
];

export default function Sidebar({ bridgeConnected }: { bridgeConnected: boolean }) {
  return (
    <div className="h-full flex flex-col pt-6 pb-4 w-[240px]">
      {/* Brand */}
      <div className="px-6 flex items-center gap-2 mb-8">
        <div className="w-2 h-2 rounded-full bg-accent-violet shadow-[0_0_10px_rgba(124,58,237,0.8)]"></div>
        <h1 className="text-xl font-bold tracking-tight">Aria</h1>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 space-y-1">
        {NAV_ITEMS.map((item, idx) => {
          const Icon = item.icon;
          return (
            <button
              key={idx}
              className={clsx(
                "w-full flex items-center gap-3 px-3 py-2 text-sm font-medium rounded-lg transition-all duration-200 group relative",
                item.active 
                  ? "bg-white/5 text-white" 
                  : "text-text-secondary hover:bg-white/[0.03] hover:text-white"
              )}
            >
              {item.active && (
                <div className="absolute left-0 top-1.5 bottom-1.5 w-[3px] bg-accent-violet rounded-r-md"></div>
              )}
              <Icon className={clsx("w-4 h-4", item.active ? "text-accent-violet" : "text-text-secondary group-hover:text-white")} />
              {item.label}
            </button>
          );
        })}
      </nav>

      {/* Status & User */}
      <div className="px-4 mt-auto flex flex-col gap-4">
        {/* Connection Status */}
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-black/40 border border-white/5">
          <div className={`w-2 h-2 rounded-full ${bridgeConnected ? 'bg-green-500 animate-pulse-slow' : 'bg-red-500'}`}></div>
          <span className={`text-[11px] font-mono capitalize tracking-wider ${bridgeConnected ? 'text-green-400' : 'text-red-400'}`}>{bridgeConnected ? 'Bridge Active' : 'Disconnected'}</span>
        </div>

        {/* User Card */}
        <div className="flex items-center gap-3 px-2 py-2 rounded-lg hover:bg-white/5 cursor-pointer transition-colors border border-transparent hover:border-white/5">
          <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-accent-violet to-accent-cyan flex items-center justify-center shrink-0">
            <User className="w-4 h-4 text-white" />
          </div>
          <div className="flex flex-col overflow-hidden">
            <span className="text-sm font-medium truncate">Aadi Chauhan</span>
            <span className="text-[10px] text-text-secondary uppercase tracking-widest">Pro Plan</span>
          </div>
        </div>
      </div>
    </div>
  );
}
