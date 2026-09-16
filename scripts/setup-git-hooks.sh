#!/usr/bin/env bash
# Script to set up local git hooks preventing direct pushes to main branch

HOOK_DIR="$(git rev-parse --git-path hooks)"
PRE_PUSH_HOOK="$HOOK_DIR/pre-push"

echo "Installing local pre-push hook to prevent direct pushes to main..."

cat << 'EOF' > "$PRE_PUSH_HOOK"
#!/usr/bin/env bash

protected_branch="main"
current_branch=$(git symbolic-ref HEAD | sed -e 's,.*/\(.*\),\1')

while read local_ref local_sha remote_ref remote_sha
do
    if [ "$remote_ref" = "refs/heads/$protected_branch" ]; then
        echo "❌ ERROR: Direct push to '$protected_branch' is restricted by repository policy."
        echo "👉 Please push your changes to a feature branch and create a Pull Request on GitHub."
        exit 1
    fi
done

exit 0
EOF

chmod +x "$PRE_PUSH_HOOK"
echo "✅ Git pre-push hook successfully installed in $PRE_PUSH_HOOK!"
