import React, { useState } from 'react';
import { X, Mic2, Component, Piano, Layers } from 'lucide-react';
import { clsx } from 'clsx';

interface AddTrackModalProps {
  onAdd: (type: string, name: string) => void;
  onClose: () => void;
}

const TRACK_TYPES = [
  { id: 'instrument', label: 'Instrument', icon: Piano, desc: 'Virtual instruments like Serum or Vital' },
  { id: 'audio', label: 'Audio', icon: Mic2, desc: 'Record vocals, guitars, or drop samples' },
  { id: 'midi', label: 'MIDI', icon: Component, desc: 'Empty MIDI track for routing' },
  { id: 'bus', label: 'Bus', icon: Layers, desc: 'Group tracks or effects returns' },
];

export default function AddTrackModal({ onAdd, onClose }: AddTrackModalProps) {
  const [selectedType, setSelectedType] = useState('audio');
  const [name, setName] = useState('');

  const handleCreate = () => {
    onAdd(selectedType, name || selectedType);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg bg-bg-panel border border-white/10 rounded-2xl shadow-2xl overflow-hidden flex flex-col">
        <div className="px-6 py-4 border-b border-white/5 flex items-center justify-between">
          <h3 className="text-lg font-bold">Add Track</h3>
          <button onClick={onClose} className="p-1.5 hover:bg-white/10 rounded text-text-secondary hover:text-white transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6">
          <label className="block text-sm font-medium text-text-secondary mb-3">Track Type</label>
          <div className="grid grid-cols-2 gap-3 mb-6">
            {TRACK_TYPES.map((type) => {
              const Icon = type.icon;
              const isSelected = selectedType === type.id;
              return (
                <button
                  key={type.id}
                  onClick={() => setSelectedType(type.id)}
                  className={clsx(
                    "text-left p-4 rounded-xl border transition-all hover:bg-white/5",
                    isSelected ? "border-accent-violet bg-accent-violet/10" : "border-white/10 bg-black/40"
                  )}
                >
                  <Icon className={clsx("w-5 h-5 mb-2", isSelected ? "text-accent-violet" : "text-white/70")} />
                  <div className="font-semibold text-sm mb-1">{type.label}</div>
                  <div className="text-xs text-text-secondary line-clamp-2">{type.desc}</div>
                </button>
              );
            })}
          </div>

          <label className="block text-sm font-medium text-text-secondary mb-2">Track Name (Optional)</label>
          <input
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleCreate()}
            placeholder="e.g. Lead Vocal"
            className="w-full bg-black/50 border border-white/10 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-accent-violet focus:ring-1 focus:ring-accent-violet transition-colors"
            autoFocus
          />
        </div>

        <div className="px-6 py-4 bg-black/50 border-t border-white/5 flex justify-end gap-3">
          <button onClick={onClose} className="px-5 py-2.5 rounded-lg text-sm font-medium hover:bg-white/5 transition-colors">
            Cancel
          </button>
          <button
            onClick={handleCreate}
            className="px-5 py-2.5 bg-accent-violet hover:bg-accent-violet/90 text-white rounded-lg text-sm font-semibold transition-colors shadow-[0_0_15px_rgba(124,58,237,0.4)]"
          >
            Create Track
          </button>
        </div>
      </div>
    </div>
  );
}
