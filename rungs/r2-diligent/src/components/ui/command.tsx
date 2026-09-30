// shadcn/ui Command (style base-nova), same classes as rungs/r1-vite/src/components/ui/command.tsx,
// with cmdk's primitives replaced by plain elements carrying the same ARIA roles and data-*
// attributes cmdk renders (see README "Why not cmdk's components"). Only the parts the palette uses.
import * as React from "react"
import { cn } from "cn"
import { SearchIcon, CheckIcon } from "lucide-react"

import { InputGroup, InputGroupAddon } from "@/components/ui/input-group"

function Command({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="command"
      tabIndex={-1}
      className={cn(
        "flex size-full flex-col overflow-hidden rounded-xl! bg-popover p-1 text-popover-foreground",
        className
      )}
      {...props}
    />
  )
}

function CommandInput({ className, ...props }: React.ComponentProps<"input">) {
  return (
    <div data-slot="command-input-wrapper" className="p-1 pb-0">
      <InputGroup className="h-8! rounded-lg! border-input/30 bg-input/30 shadow-none! *:data-[slot=input-group-addon]:pl-2!">
        <input
          data-slot="command-input"
          type="text"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={true}
          className={cn(
            "w-full text-sm outline-hidden disabled:cursor-not-allowed disabled:opacity-50",
            className
          )}
          {...props}
        />
        <InputGroupAddon>
          <SearchIcon className="size-4 shrink-0 opacity-50" />
        </InputGroupAddon>
      </InputGroup>
    </div>
  )
}

function CommandList({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="command-list"
      role="listbox"
      tabIndex={-1}
      className={cn(
        "no-scrollbar max-h-72 scroll-py-1 overflow-x-hidden overflow-y-auto outline-none",
        className
      )}
      {...props}
    />
  )
}

function CommandEmpty({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="command-empty"
      role="presentation"
      className={cn("py-6 text-center text-sm", className)}
      {...props}
    />
  )
}

// Hoisted: the (invisible unless checked) icon shadcn puts in every item, created once.
const checkIcon = (
  <CheckIcon className="ml-auto opacity-0 group-has-data-[slot=command-shortcut]/command-item:hidden group-data-[checked=true]/command-item:opacity-100" />
)

function CommandItem({
  className,
  children,
  selected,
  ...props
}: React.ComponentProps<"div"> & { selected: boolean }) {
  return (
    <div
      data-slot="command-item"
      role="option"
      aria-disabled={false}
      aria-selected={selected}
      data-disabled={false}
      data-selected={selected}
      className={cn(
        "group/command-item relative flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-hidden select-none in-data-[slot=dialog-content]:rounded-lg! data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50 data-selected:bg-muted data-selected:text-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 data-selected:*:[svg]:text-foreground",
        className
      )}
      {...props}
    >
      {children}
      {checkIcon}
    </div>
  )
}

export { Command, CommandInput, CommandList, CommandEmpty, CommandItem }
