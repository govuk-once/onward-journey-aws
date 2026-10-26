import { marked } from 'marked';
import DOMPurify from 'dompurify';

marked.setOptions({ breaks: true });

export async function markdownToHtml(markdown: string): Promise<string> {
  // Ensure headers have a newline before them if they follow text directly
  // and are not at the start of a line or preceded by other hashes/whitespace
  const processedMarkdown = markdown.replace(/(?<=[^\s\n#])(#{1,6}\s)/g, '\n\n$1');
  const html = await marked(processedMarkdown);

  if (typeof window !== 'undefined') {
    return DOMPurify.sanitize(html);
  } else {
    // server-side
    const sanitizeHtml = (await import('sanitize-html')).default;
    return sanitizeHtml(html);
  }
}
