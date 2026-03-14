import React from 'react';
import { Sparkles, Drum, Music, Mic2, Component } from 'lucide-react';

const PILLS = [
  { icon: Drum, label: 'Add Drums', color: 'text-orange-400', message: 'Add a drums track with a basic pattern at the current tempo' },
  { icon: Component, label: 'Add Bass', color: 'text-blue-400', message: 'Add a bass track with a simple bass line' },
  { icon: Music, label: 'Add Melody', color: 'text-green-400', message: 'Add a melody track with a musical lead line' },
  { icon: Mic2, label: 'Add Vocals', color: 'text-pink-400', message: 'Add an empty vocals track ready for recording' },
];

interface ActionPillsProps {
  onSendMessage: (content: string) => void;
}

export default function ActionPills({ onSendMessage }: ActionPillsProps) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-3 mb-8">
      {PILLS.map((pill, idx) => {
        const Icon = pill.icon;
        return (
          <button
            key={idx}
            onClick={() => onSendMessage(pill.message)}
            className="group flex items-center gap-2 px-4 py-2 bg-gradient-to-b from-white/10 to-transparent border border-white/10 rounded-full hover:border-white/30 hover:bg-white/5 transition-all shadow-sm hover:shadow-[0_0_15px_rgba(255,255,255,0.05)] active:scale-95"
          >
            <Icon className={`w-3.5 h-3.5 ${pill.color} group-hover:scale-110 transition-transform`} />
            <span className="text-xs font-medium text-white/90 group-hover:text-white">{pill.label}</span>
            <Sparkles className="w-3 h-3 text-white/20 group-hover:text-accent-violet transition-colors ml-1" />
          </button>
        );
      })}
    </div>
  );
}
