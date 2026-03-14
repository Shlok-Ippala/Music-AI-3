import React from 'react';
import { Layers, FilePlus, Copy, FolderOpen } from 'lucide-react';

export default function EmptyProjectState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-8 w-full max-w-4xl mx-auto min-h-[500px]">
      <div className="w-20 h-20 mb-6 rounded-full bg-white/5 border border-white/10 flex items-center justify-center">
        <Layers className="w-8 h-8 text-text-secondary" />
      </div>
      
      <h2 className="text-3xl font-bold tracking-tight mb-2">Start creating</h2>
      <p className="text-text-secondary mb-12">Choose how you'd like to begin your session.</p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 w-full max-w-3xl">
        <button className="group flex flex-col items-center justify-center gap-4 p-8 bg-black/30 border border-white/5 rounded-2xl hover:bg-white/5 hover:border-white/10 transition-all hover:-translate-y-1 hover:shadow-xl">
          <div className="w-12 h-12 rounded-full bg-accent-violet/20 text-accent-violet flex items-center justify-center group-hover:scale-110 transition-transform">
            <FilePlus className="w-5 h-5" />
          </div>
          <span className="font-semibold">Start from scratch</span>
        </button>

        <button className="group flex flex-col items-center justify-center gap-4 p-8 bg-black/30 border border-white/5 rounded-2xl hover:bg-white/5 hover:border-white/10 transition-all hover:-translate-y-1 hover:shadow-xl">
          <div className="w-12 h-12 rounded-full bg-accent-cyan/20 text-accent-cyan flex items-center justify-center group-hover:scale-110 transition-transform">
            <Copy className="w-5 h-5" />
          </div>
          <span className="font-semibold">Use a template</span>
        </button>

        <button className="group flex flex-col items-center justify-center gap-4 p-8 bg-black/30 border border-white/5 rounded-2xl hover:bg-white/5 hover:border-white/10 transition-all hover:-translate-y-1 hover:shadow-xl">
          <div className="w-12 h-12 rounded-full bg-white/10 text-white flex items-center justify-center group-hover:scale-110 transition-transform">
            <FolderOpen className="w-5 h-5" />
          </div>
          <span className="font-semibold">Open existing project</span>
        </button>
      </div>
    </div>
  );
}
