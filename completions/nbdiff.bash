# bash completion for nbdiff                                  -*- shell-script -*-
# Enable with:  source /path/to/nbdiff/completions/nbdiff.bash
# (or copy it to ~/.local/share/bash-completion/completions/nbdiff)

_nbdiff() {
    local cur prev words cword
    COMPREPLY=()
    if declare -F _init_completion >/dev/null 2>&1; then
        _init_completion -n = || return
    else
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
    fi

    local opts="--outputs --no-outputs --metadata --no-metadata --execution-count
        --no-execution-count --all --sources-only --visualize= --match= --match-threshold=
        --no-moves --ignore-metadata= --ignore-mime= --unchanged= --suppress-common-cells
        --max-lines= --stat --json --no-summary --no-intraline --diff --git --cached --staged
        --normal --brief --report-identical-files --context --unified --side-by-side --width=
        --left-column --suppress-common-lines --recursive --new-file --unidirectional-new-file
        --exclude= --exclude-from= --starting-file= --from-file= --to-file= --ignore-case
        --ignore-tab-expansion --ignore-trailing-space --ignore-space-change --ignore-all-space
        --ignore-blank-lines --ignore-matching-lines= --strip-trailing-cr --minimal
        --speed-large-files --expand-tabs --initial-tab --tabsize= --label --color --color=
        --help --version"

    _nbdiff_notebooks() {
        local IFS=$'\n'
        COMPREPLY+=( $(compgen -f -X '!*.ipynb' -- "$1") $(compgen -d -- "$1") )
    }

    case "$prev" in
        --visualize|--from-file|--to-file|-S|--starting-file)
            _nbdiff_notebooks "$cur"; return ;;
        -X|--exclude-from)
            COMPREPLY=( $(compgen -f -- "$cur") ); return ;;
        --match)
            COMPREPLY=( $(compgen -W "auto id content" -- "$cur") ); return ;;
        --unchanged)
            COMPREPLY=( $(compgen -W "collapse list show hide" -- "$cur") ); return ;;
        -U|-C|-W|--width|--tabsize|--max-lines|--match-threshold|-I|--ignore-matching-lines|\
        --label|-x|--exclude|--ignore-metadata|--ignore-mime)
            return ;;
    esac

    case "$cur" in
        --color=*)
            COMPREPLY=( $(compgen -W "never always auto" -- "${cur#*=}") ); return ;;
        --unchanged=*)
            COMPREPLY=( $(compgen -W "collapse list show hide" -- "${cur#*=}") ); return ;;
        --match=*)
            COMPREPLY=( $(compgen -W "auto id content" -- "${cur#*=}") ); return ;;
        --visualize=*|--from-file=*|--to-file=*)
            local IFS=$'\n'
            COMPREPLY=( $(compgen -f -X '!*.ipynb' -- "${cur#*=}") $(compgen -d -- "${cur#*=}") )
            return ;;
        -*)
            COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
            [[ ${#COMPREPLY[@]} -eq 1 && ${COMPREPLY[0]} == *= ]] && compopt -o nospace 2>/dev/null
            return ;;
    esac

    local w git_mode=0
    for w in "${words[@]}"; do
        [[ $w == --diff || $w == --git ]] && git_mode=1
    done
    if (( git_mode )); then
        local IFS=$'\n'
        COMPREPLY=( $(compgen -W "$(git for-each-ref --format='%(refname:short)' 2>/dev/null) HEAD" -- "$cur") )
        _nbdiff_notebooks "$cur"
    else
        _nbdiff_notebooks "$cur"
    fi
}
complete -o filenames -F _nbdiff nbdiff
