import React, { useState, useRef, useEffect } from 'react';
import { Mic, Send, MicOff } from 'lucide-react';
import { clsx } from 'clsx';
import ChatBubble, { type MessageProps } from '../chat/ChatBubble';

const SUGGESTIONS = ["What's my tempo?", "Add reverb to snare", "Create a bass line", "Summarize my project"];

interface RightPanelProps {
  messages: MessageProps[];
  isThinking: boolean;
  onSendMessage: (content: string) => void;
}

export default function RightPanel({ messages, isThinking, onSendMessage }: RightPanelProps) {
  const [val, setVal] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = () => {
    const content = val.trim();
    if (!content || isThinking) return;
    onSendMessage(content);
    setVal('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setVal(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
  };

  return (
    <div className="flex flex-col h-full relative">
      {/* Header */}
      <div className="h-12 border-b border-white/5 flex items-center justify-between px-5 shrink-0 bg-bg-panel/50 backdrop-blur-sm z-10">
        <div className="flex items-center gap-2">
          <div className={clsx(
            "w-2 h-2 rounded-full shadow-[0_0_8px_rgba(124,58,237,0.8)]",
            isThinking ? "bg-accent-cyan animate-pulse" : "bg-accent-violet animate-pulse"
          )}></div>
          <h2 className="font-semibold text-sm tracking-wide">Aria</h2>
        </div>
        {isThinking && (
          <span className="text-[11px] text-text-secondary font-mono animate-pulse">thinking...</span>
        )}
      </div>

      {/* Chat History */}
      <div className="flex-1 overflow-y-auto p-5 space-y-4 flex flex-col pt-6 custom-scrollbar">
        {messages.length === 0 && (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center px-4">
            <div className="w-8 h-8 rounded-full bg-accent-violet/20 flex items-center justify-center">
              <div className="w-2 h-2 rounded-full bg-accent-violet"></div>
            </div>
            <p className="text-text-secondary text-sm">Ask Aria to create something, or pick a suggestion below.</p>
          </div>
        )}
        {messages.map(msg => (
          <ChatBubble key={msg.id} {...msg} />
        ))}
        <div ref={bottomRef} className="h-4 shrink-0"></div>
      </div>

      {/* Input Area */}
      <div className="p-4 border-t border-white/5 bg-bg-panelLight/60 backdrop-blur-md shrink-0 relative z-10">
        {/* Suggestion Chips */}
        <div className="flex overflow-x-auto gap-2 mb-3 pb-1 hide-scrollbar">
          {SUGGESTIONS.map((chip, idx) => (
            <button
              key={idx}
              onClick={() => { setVal(chip); textareaRef.current?.focus(); }}
              className="whitespace-nowrap text-[11px] font-medium px-3 py-1.5 rounded-full border border-white/10 text-text-secondary hover:text-white hover:border-white/20 transition-all hover:bg-white/5 active:scale-95"
            >
              {chip}
            </button>
          ))}
        </div>

        {/* Input Box */}
        <div className="relative flex flex-col group mt-1">
          <textarea
            ref={textareaRef}
            rows={1}
            value={val}
            onChange={handleTextareaChange}
            onKeyDown={handleKeyDown}
            placeholder="Tell Aria what to do..."
            disabled={isThinking}
            className="w-full bg-black/50 border border-white/10 rounded-2xl py-3.5 pl-12 pr-12 text-sm text-white placeholder:text-text-secondary focus:outline-none focus:border-accent-violet/50 focus:ring-1 focus:ring-accent-violet/50 resize-none transition-all custom-scrollbar disabled:opacity-50"
            style={{ minHeight: '48px', maxHeight: '120px' }}
          />

          <button
            onMouseDown={() => setIsRecording(true)}
            onMouseUp={() => setIsRecording(false)}
            onMouseLeave={() => setIsRecording(false)}
            className="absolute left-2 top-2 w-9 h-9 rounded-full hover:bg-white/10 flex items-center justify-center cursor-pointer transition-colors z-10 text-text-secondary hover:text-white"
          >
            {isRecording ? (
              <>
                <MicOff className="w-4 h-4 text-accent-violet" />
                <span className="absolute inset-0 rounded-full border border-accent-violet animate-ripple"></span>
              </>
            ) : (
              <Mic className="w-4 h-4" />
            )}
          </button>

          <button
            onClick={handleSend}
            disabled={!val.trim() || isThinking}
            className={clsx(
              "absolute right-2 top-2 w-9 h-9 rounded-xl flex items-center justify-center transition-all",
              val.trim() && !isThinking
                ? "bg-accent-violet text-white hover:bg-accent-violet/90 hover:scale-105 active:scale-95 shadow-[0_0_15px_rgba(124,58,237,0.3)]"
                : "bg-white/5 text-text-secondary cursor-not-allowed"
            )}
          >
            <Send className="w-4 h-4 mr-0.5 mt-0.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
