import React from 'react';
import { Settings } from 'lucide-react';

interface ToolChipProps {
  toolName: string;
  args: string;
  delayIndex: number;
}

export default function ToolChip({ toolName, args, delayIndex }: ToolChipProps) {
  return (
    <div 
      className="flex items-center gap-2 bg-black/60 border border-white/5 border-l-2 border-l-accent-violet rounded px-2.5 py-1.5 w-max font-mono text-[11px] text-text-secondary animate-slide-in opacity-0 shadow-sm"
      style={{ animationDelay: `${delayIndex * 80}ms` }}
    >
      <Settings className="w-3 h-3 text-text-secondary/80" />
      <span>
        <span className="text-accent-cyan/90">{toolName}</span>
        <span className="text-white/40">(</span>
        <span className="text-white/80">"{args}"</span>
        <span className="text-white/40">)</span>
      </span>
    </div>
  );
}
