// maximal munch tests - longest match at each position
// == should be single token, not two =
a==b
// != single token
a!=b
// <= single token
a<=b
// >= single token
a>=b
// && single token
a&&b
// || single token
a||b
// -> single token
a->b
// === => == then =
a===b
// !== => != then =
a!==b
// <== => <= then =
a<==b
// >== => >= then =
a>==b
// &&& => && then & is LEX_INVALID_CHAR for single & (tested in bad fixture)
// (removed a&&&b from good fixture to keep it valid)
// ||| => || then | is LEX_INVALID_CHAR for single | (tested in bad fixture)
// (removed a|||b from good fixture)
// --> => - then ->
a-->b
// ->> => -> then >
a->>b
// == != <= >= without spaces
x==y!=z<=w>=v
// && || -> combined
p&&q||r->s
// comment marker // should be comment not two /
a// comment
b
// edge: / then // would be slash then comment? Actually // starts comment, so a//b is a then comment
x// y
// verify -> is not - and >
let arrow = ->;
