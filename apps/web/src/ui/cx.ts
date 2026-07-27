/**
 * Join class names, dropping anything absent.
 *
 *     cx('button', 'buttonPrimary', styles.navButton)
 *     cx(styles.navLink, isActive && styles.navLinkActive)
 *
 * It exists because CSS Modules are typed as possibly-undefined lookups and this project runs
 * `exactOptionalPropertyTypes`, under which `className={styles.thing}` is a type error rather than
 * a silent `undefined` in the DOM.
 *
 * One module rather than a copy per component: three private copies is how the accepted types
 * drift apart, and how a fix to one of them silently misses the others. Not `clsx`, only because
 * the whole implementation is one line and a dependency has a lockfile entry, a supply chain and
 * an upgrade cadence.
 */
export function cx(...names: Array<string | false | null | undefined>): string {
  return names.filter(Boolean).join(' ')
}
