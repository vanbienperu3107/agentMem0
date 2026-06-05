import { useState, useEffect, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { useHotkeys } from 'react-hotkeys-hook';
import useInfo from '~hooks/useInfo';
import SendIcon from '~icons/Send';
import debounce from 'lodash/debounce';

// Khớp chính xác "compact", "/compact", "lưu", "/lưu" (không phân biệt hoa thường,
// cho phép khoảng trắng đầu/cuối). Tránh trigger khi user gõ từ này trong câu khác.
const COMPACT_KEYWORD = /^\s*\/?(compact|lưu|luu)\s*$/i;

export default function ChatInput() {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [message, setMessage] = useState('');
  const { isMac } = useInfo();

  useEffect(() => {
    // Không sync sang chatgpt.com nếu là keyword compact/lưu
    if (COMPACT_KEYWORD.test(message)) return;

    const syncMessage = debounce(async () => {
      try {
        await invoke('ask_sync', { message: JSON.stringify(message) });
      } catch (error) {
        console.error('Error syncing message:', error);
      }
    }, 300); // Debounce by 300ms

    syncMessage();
    return () => syncMessage.cancel(); // Cleanup debounce on unmount
  }, [message]);

  useHotkeys(isMac ? 'meta+enter' : 'ctrl+enter', async (event: KeyboardEvent) => {
    event.preventDefault();
    await handleSend();
  }, {
    enableOnFormTags: true,
  }, [message]);

  const clearInput = () => {
    setMessage('');
    if (inputRef.current) {
      inputRef.current.value = '';
      inputRef.current.focus();
    }
  };

  const handleCompact = async () => {
    try {
      const path = await invoke<string | null>('compact_session');
      if (path) {
        console.log('[compact] exported:', path);
      } else {
        console.log('[compact] no messages to export — session rotated');
      }
    } catch (err) {
      console.error('[compact] failed:', err);
    }
    clearInput();
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setMessage(value);
    // Trigger ngay khi vừa gõ xong keyword (không cần bấm Send)
    if (COMPACT_KEYWORD.test(value)) {
      handleCompact();
    }
  };

  const handleSend = async () => {
    if (!message) return;
    // Nếu user bấm Send với nội dung là keyword → vẫn xuất file thay vì gửi lên ChatGPT
    if (COMPACT_KEYWORD.test(message)) {
      await handleCompact();
      return;
    }
    try {
      await invoke('ask_send', { message: JSON.stringify(message) });
    } catch (error) {
      console.error('Error sending message:', error);
    }
    clearInput();
  };

  return (
    <div className="relative flex h-full dark:bg-app-gray-2/[0.98] bg-gray-100 dark:text-slate-200 items-center gap-1">
      <textarea
        ref={inputRef}
        onChange={handleInput}
        spellCheck="false"
        autoFocus
        className="w-full h-full pl-3 pr-[40px] py-2 outline-none resize-none bg-transparent"
        placeholder="Type your message here... (gõ 'compact' hoặc 'lưu' để xuất file JSON)"
      />
      <SendIcon
        size={30}
        className="absolute right-2 text-gray-400/80 dark:text-gray-600 cursor-pointer"
        onClick={handleSend}
        title={`Send message (${isMac ? '⌘⏎' : '⌃⏎'})`}
        aria-label="Send message"
      />
    </div>
  );
}
