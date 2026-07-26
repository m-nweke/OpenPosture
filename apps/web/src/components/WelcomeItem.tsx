import type { ReactNode } from 'react'
import styles from './WelcomeItem.module.css'

/**
 * Vue named slots → React props that happen to hold JSX.
 *
 *   Vue:    <template #icon><DocumentationIcon /></template>
 *   React:  icon={<DocumentationIcon />}
 *
 * Vue's *default* slot maps to the special `children` prop. There's no separate
 * slot mechanism in React — "a slot" is just a prop whose value is an element.
 */
export default function WelcomeItem({
  icon,
  heading,
  children,
}: {
  icon: ReactNode
  heading: ReactNode
  children: ReactNode
}) {
  return (
    <div className={styles.item}>
      <i className={styles.icon}>{icon}</i>
      <div className={styles.details}>
        <h3>{heading}</h3>
        {children}
      </div>
    </div>
  )
}
