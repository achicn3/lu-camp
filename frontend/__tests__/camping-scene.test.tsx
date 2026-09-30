// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { CampingScene } from '@/features/customer-display/CampingScene';
const mocks = vi.hoisted(() => ({
 drive: { destroy: vi.fn(), seek: vi.fn(), snapshot: () => ({ time: 37 }) },
 legacy: { destroy: vi.fn(), setMode: vi.fn(), bump: vi.fn() },
 createDrive: vi.fn(), createLegacy: vi.fn(),
}));
vi.mock('@/features/customer-display/camping/drive-scene', () => ({ buildDriveHtml: () => '<svg data-art="drive"/>', createDriveController: mocks.createDrive.mockImplementation(() => mocks.drive) }));
vi.mock('@/features/customer-display/camping/world', () => ({ buildSceneHtml: () => '<svg data-art="legacy"/>' }));
vi.mock('@/features/customer-display/camping/timeline', () => ({ createCampingController: mocks.createLegacy.mockImplementation(() => mocks.legacy) }));
vi.mock('gsap', () => ({ gsap: { context: (fn: () => void) => { fn(); return { add: (f: () => void) => f(), revert: vi.fn() }; } } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
it('switches approved scenes, stops during documents, resumes the road and reacts to added items', () => {
 SVGGraphicsElement.prototype.getBBox = vi.fn();
 const { container, rerender } = render(<CampingScene mode="idle" />);
 expect(container.querySelector('[data-art="drive"]')).not.toBeNull();
 rerender(<CampingScene mode="cart" itemCount={1} />);
 expect(mocks.drive.destroy).toHaveBeenCalledOnce();
 expect(container.querySelector('[data-art="drive"]')).toBeNull();
 expect(container.querySelector('[data-art="legacy"]')).not.toBeNull();
 expect(mocks.legacy.bump).toHaveBeenCalledOnce();
 rerender(<CampingScene mode="cart" itemCount={2} />);
 expect(mocks.legacy.bump).toHaveBeenCalledTimes(2);
 rerender(<CampingScene mode="paid" itemCount={2} />);
 expect(mocks.createLegacy).toHaveBeenCalledOnce();
 expect(mocks.legacy.setMode).toHaveBeenLastCalledWith('paid');
 rerender(<CampingScene mode="hidden" />);
 expect(mocks.legacy.destroy).toHaveBeenCalledOnce();
 expect(container.querySelector('svg')).toBeNull();
 rerender(<CampingScene mode="celebrate" />);
 expect(mocks.createLegacy).toHaveBeenLastCalledWith(expect.anything(), false, 'celebrate');
 rerender(<CampingScene mode="idle" />);
 expect(mocks.drive.seek).toHaveBeenLastCalledWith(37);
});
