import { describe, it, expect } from '@jest/globals';
import { markdownToHtml } from './markdown';

describe('markdownToHtml', () => {
  it('should render a standard markdown header correctly', async () => {
    const markdown = '## My Header';
    const html = await markdownToHtml(markdown);
    expect(html).toContain('<h2>My Header</h2>');
  });

  it('should render a header even if it follows text without a newline', async () => {
    const markdown = 'I found a DVLA service for you. Let me check if agents are available right now.## DVLA Contact Details';
    const html = await markdownToHtml(markdown);
    expect(html).toContain('<h2>DVLA Contact Details</h2>');
    expect(html).not.toContain('#')
  });

  it('should not break normal text that happens to have a hash', async () => {
    const markdown = 'This is question # 1 for you';
    const html = await markdownToHtml(markdown);
    expect(html).toContain('This is question # 1 for you');
    expect(html).not.toContain('<h1>');
  });

  it('should handle multiple headers correctly', async () => {
    const markdown = 'Text here.## Header 1\nSome more text.### Header 2';
    const html = await markdownToHtml(markdown);
    expect(html).toContain('<h2>Header 1</h2>');
    expect(html).toContain('<h3>Header 2</h3>');
  });
});
