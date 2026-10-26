#!/usr/bin/env bash
# Generated from branding/veyra-logo.svg by scripts/generate-fetch-logos.py.
if [[ "$no_color" != "1" ]]; then
    c1=$(getColor 'light cyan')
    c2=$(getColor 'white')
else
    c1=""
    c2=""
fi
if [ -n "${my_lcolor}" ]; then c1="${my_lcolor}"; c2="${my_lcolor}"; fi
startline="0"
logowidth="38"
fulloutput=(
"             ${c2}++%s"
"            ${c2}++++%s"
"           ${c1}######%s"
"          ${c1}########      ${c2}++%s"
"         ${c1}##########   ##${c2}+++%s"
"        ${c1}#####################%s"
"      ${c1}#######  ###############%s"
"     ${c1}######      ###############%s"
"    ${c1}######        ###############%s"
"   ${c1}######          #### ##########%s"
"  ${c1}######            ##    ##########%s"
" ${c1}######                    ##########%s"
"             ${c1}###         ##%s"
"             ${c1}###%s"
)
