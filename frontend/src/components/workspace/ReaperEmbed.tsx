import React from 'react';
import { ExternalLink } from 'lucide-react';

export default function ReaperEmbed() {
  return (
    <div className="w-full max-w-5xl mx-auto flex-1 flex flex-col items-center justify-center border border-white/10 bg-black/40 rounded-2xl p-8 backdrop-blur-md relative overflow-hidden min-h-[400px]">
      <div className="absolute inset-0 noise-bg opacity-30"></div>
      
      <div className="relative z-10 flex flex-col items-center text-center max-w-md">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-green-500/20 to-blue-500/20 border border-white/10 flex items-center justify-center mb-6 shadow-2xl">
          <img src="https://www.reaper.fm/favicon.ico" alt="REAPER Logo" className="w-8 h-8 opacity-80" onError={(e) => e.currentTarget.style.display = 'none'} />
        </div>
        
        <h3 className="text-xl font-bold mb-2">REAPER Interface</h3>
        <p className="text-sm text-text-secondary mb-8 leading-relaxed">
          Pro Mode is active. Aria is connected and ready to scaffold tracks, mix, and automate within your existing REAPER session.
        </p>
        
        <button className="px-6 py-3 bg-white text-black font-semibold rounded-lg flex items-center gap-2 hover:bg-white/90 transition-all hover:scale-[0.98]">
          <ExternalLink className="w-4 h-4" />
          Launch & Connect REAPER
        </button>
      </div>
    </div>
  );
}
