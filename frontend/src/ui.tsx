import { Alert, Button, Dialog, Select } from '@gravity-ui/uikit'
import type { ButtonButtonProps } from '@gravity-ui/uikit'
import type { ReactNode } from 'react'

export function Action({className = '', type = 'button', size, view, children, 'aria-pressed': pressed, ...props}: ButtonButtonProps) {
  const variants = className.split(' ')
  const buttonView = view ?? (variants.includes('primary') ? 'action'
    : variants.some(value => ['active', 'current', 'logout', 'icon', 'link', 'subtle', 'nav-action'].includes(value))
      ? 'flat' : 'outlined')
  return <Button {...props} aria-pressed={pressed} selected={props.selected ?? pressed === true} type={type} className={className} view={buttonView}
    size={size ?? (variants.includes('small') || variants.includes('icon') ? 'm' : 'l')}>
    <span className="action-content">{children}</span>
  </Button>
}

export function Feedback({tone = 'info', children}: {tone?: 'info' | 'success' | 'warning' | 'danger'; children: ReactNode}) {
  return <div role={tone === 'danger' ? 'alert' : 'status'}>
    <Alert theme={tone === 'info' ? 'info' : tone} message={children}/>
  </div>
}

export function Selection({label, value, onChange, options, placeholder, disabled, filterable}: {
  label: string; value: string; onChange: (value: string) => void; options: {value: string; content: string}[];
  placeholder?: string; disabled?: boolean; filterable?: boolean
}) {
  return <Select aria-label={label} width="max" size="l" options={options}
    value={value ? [value] : []} onUpdate={values => onChange(values[0] ?? '')}
    placeholder={placeholder} disabled={disabled} filterable={filterable} filterPlaceholder="Поиск"
    hasClear={!!placeholder}/>
}

export function Confirm({title, open, onClose, onApply, apply, children}: {
  title: string; open: boolean; onClose: () => void; onApply: () => void; apply: string; children: ReactNode
}) {
  return <Dialog open={open} onOpenChange={next => { if (!next) onClose() }}
    aria-label={title} initialFocus="cancel" maxWidth="s" fullWidth>
    <Dialog.Header caption={title}/>
    <Dialog.Body>{children}</Dialog.Body>
    <Dialog.Footer textButtonApply={apply} textButtonCancel="Отмена"
      onClickButtonApply={onApply} onClickButtonCancel={onClose}/>
  </Dialog>
}
