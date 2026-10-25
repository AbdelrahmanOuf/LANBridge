$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

function As-Array($value) { return ,@($value) }
function Adapter($p) {
    $a = Get-NetAdapter -IncludeHidden | Where-Object { $_.ifIndex -eq [int]$p.index }
    if (-not $a -or $a.Count -gt 1 -or ([string]$a.InterfaceGuid).Trim('{}') -ne ([string]$p.guid).Trim('{}')) {
        throw 'Adapter identity changed. Refresh and choose the adapter again.'
    }
    return $a
}
function Network-Snapshot($p) {
    $a = Adapter $p
    $i = Get-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4
    $dns = Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{' + ([string]$a.InterfaceGuid).Trim('{}') + '}'
    $staticDns = (Get-ItemProperty -LiteralPath $key -Name NameServer -ErrorAction SilentlyContinue).NameServer
    $profile = Get-NetConnectionProfile -InterfaceIndex $a.ifIndex -ErrorAction SilentlyContinue
    return @{
        index=[int]$a.ifIndex; guid=([string]$a.InterfaceGuid).Trim('{}'); name=$a.Name;
        dhcp=([string]$i.Dhcp -eq 'Enabled'); dns=@($dns.ServerAddresses); dns_auto=([string]::IsNullOrWhiteSpace($staticDns));
        ips=@(Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -eq 'Manual' } | ForEach-Object {
            @{ip=$_.IPAddress; prefix=[int]$_.PrefixLength; skip=[bool]$_.SkipAsSource}
        });
        routes=@(Get-NetRoute -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
            ForEach-Object { @{gateway=$_.NextHop; metric=[int]$_.RouteMetric} });
        profile= if ($profile) { [string]$profile.NetworkCategory } else { '' }
    }
}
function Reset-IPv4($a) {
    Get-NetRoute -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        Remove-NetRoute -Confirm:$false -ErrorAction Stop
    Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.PrefixOrigin -ne 'WellKnown' } | Remove-NetIPAddress -Confirm:$false -ErrorAction Stop
}
function Dns4($a, $servers, $automatic) {
    $client=Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4
    if ($automatic) {
        Set-DnsClientServerAddress -InputObject $client -ResetServerAddresses
    } else {
        Set-DnsClientServerAddress -InputObject $client -ServerAddresses @($servers)
    }
}
function Restore-Network($s) {
    $a = Adapter $s
    Reset-IPv4 $a
    if ($s.dhcp) {
        Set-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -Dhcp Enabled
    } else {
        Set-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -Dhcp Disabled
    }
    foreach ($ip in @($s.ips)) {
        New-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -IPAddress $ip.ip -PrefixLength ([int]$ip.prefix) -SkipAsSource ([bool]$ip.skip) | Out-Null
    }
    if (-not $s.dhcp) {
        foreach ($r in @($s.routes)) {
            New-NetRoute -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -NextHop $r.gateway -RouteMetric ([int]$r.metric) | Out-Null
        }
    }
    if ($s.dns_auto) {
        Dns4 $a @() $true
    } elseif (@($s.dns).Count -gt 0) {
        Dns4 $a @($s.dns) $false
    }
    return Network-Snapshot $s
}
function Share-Owned($p) {
    $s = Get-SmbShare -Name $p.name -ErrorAction Stop
    if ($s.Description -ne ('LANBridgePro:' + $p.owner)) { throw 'This share is not owned by this saved LANBridge operation.' }
    if ($s.Path -ne $p.path) { throw 'Share path changed outside this operation. Inspect it before removing.' }
    return $s
}
function Dacl($path) {
    $acl = Get-Acl -LiteralPath $path
    return $acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access)
}
function Restore-Dacl($path, $sddl, $expected) {
    if ((Dacl $path) -ne $expected) { throw 'NTFS permissions changed outside this operation. Refusing to overwrite newer permissions.' }
    $acl = Get-Acl -LiteralPath $path
    $acl.SetSecurityDescriptorSddlForm($sddl, [System.Security.AccessControl.AccessControlSections]::Access)
    Set-Acl -LiteralPath $path -AclObject $acl
}
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $p = $request.payload
    $data = switch ($request.action) {
        'adapters' {
            @(Get-NetAdapter -IncludeHidden | ForEach-Object {
                $a=$_
                $ips = @(Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue)
                $config = Get-NetIPConfiguration -InterfaceIndex $a.ifIndex -ErrorAction SilentlyContinue
                $ipif = Get-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
                $profile = Get-NetConnectionProfile -InterfaceIndex $a.ifIndex -ErrorAction SilentlyContinue
                @{
                    index=[int]$a.ifIndex; guid=([string]$a.InterfaceGuid).Trim('{}'); name=$a.Name;
                    description=$a.InterfaceDescription; mac=$a.MacAddress; status=[string]$a.Status;
                    speed=[string]$a.LinkSpeed; hardware=[bool]$a.HardwareInterface;
                    medium=[string]$a.NdisPhysicalMedium; dhcp=([string]$ipif.Dhcp -eq 'Enabled');
                    ipv4=@($ips | ForEach-Object { @{ip=$_.IPAddress; prefix=[int]$_.PrefixLength; state=[string]$_.AddressState} });
                    gateway=@($config.IPv4DefaultGateway | ForEach-Object {$_.NextHop});
                    dns=@((Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses);
                    profile= if ($profile) {[string]$profile.NetworkCategory} else {''}
                }
            })
            break
        }
        'network_snapshot' { Network-Snapshot $p; break }
        'network_apply' {
            $a = Adapter $p
            Reset-IPv4 $a
            if ($p.mode -eq 'dhcp') {
                Set-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -Dhcp Enabled
                Dns4 $a @() $true
            } else {
                Set-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -Dhcp Disabled
                $args = @{InterfaceIndex=$a.ifIndex; AddressFamily='IPv4'; IPAddress=$p.ip; PrefixLength=[int]$p.prefix}
                if ($p.gateway) {$args.DefaultGateway=$p.gateway}
                New-NetIPAddress @args | Out-Null
                if (@($p.dns).Count -gt 0) {
                    Dns4 $a @($p.dns) $false
                } else { Dns4 $a @() $true }
                for ($attempt=0; $attempt -lt 8; $attempt++) {
                    Start-Sleep -Seconds 1
                    $assigned = Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -IPAddress $p.ip
                    if ($assigned.AddressState -eq 'Duplicate') {throw 'Windows detected a duplicate IPv4 address. The saved settings will be restored.'}
                    if ($assigned.AddressState -eq 'Preferred') {break}
                }
                if ($assigned.AddressState -ne 'Preferred') {throw 'The new IPv4 did not become Preferred. Recheck cable/IP; the saved settings will be restored.'}
            }
            Network-Snapshot $p
            break
        }
        'network_restore' { Restore-Network $p.snapshot; break }
        'profile_private' {
            $a = Adapter $p
            $profile=Get-NetConnectionProfile -InterfaceIndex $a.ifIndex
            if ($profile.NetworkCategory -eq 'DomainAuthenticated') { throw 'Domain profile is managed by your organization.' }
            Set-NetConnectionProfile -InterfaceIndex $a.ifIndex -NetworkCategory Private
            Network-Snapshot $p
            break
        }
        'profile_restore' {
            $a = Adapter $p.snapshot
            if ($p.snapshot.profile -notin @('Private','Public')) { throw 'No restorable Private/Public profile in snapshot.' }
            Set-NetConnectionProfile -InterfaceIndex $a.ifIndex -NetworkCategory $p.snapshot.profile
            Network-Snapshot $p.snapshot
            break
        }
        'shares' {
            @(Get-SmbShare | ForEach-Object {
                @{name=$_.Name; path=$_.Path; description=$_.Description; special=[bool]$_.Special;
                  access=@(Get-SmbShareAccess -Name $_.Name | ForEach-Object {
                    @{account=$_.AccountName; right=[string]$_.AccessRight; type=[string]$_.AccessControlType}
                  })}
            })
            break
        }
        'share_snapshot' {
            if (Get-SmbShare -Name $p.name -ErrorAction SilentlyContinue) {throw 'Share name already exists. Generate another name.'}
            $sid = (New-Object System.Security.Principal.NTAccount($p.account)).Translate([System.Security.Principal.SecurityIdentifier])
            if ($sid.Value -in @('S-1-1-0','S-1-5-7','S-1-5-11','S-1-5-32-545','S-1-5-32-546')) {throw 'Choose a specific authorized user/group, not Everyone, Guests, Users or Anonymous.'}
            @{path=$p.path; dacl=(Dacl $p.path); sid=$sid.Value}
            break
        }
        'share_create' {
            if (Get-SmbShare -Name $p.name -ErrorAction SilentlyContinue) {throw 'Share already exists.'}
            $changedAcl=$false
            $afterAcl=$null
            try {
            if ($p.grant_ntfs) {
                if ((Dacl $p.path) -ne $p.snapshot.dacl) {throw 'NTFS ACL changed since preview. Retry.'}
                $acl=Get-Acl -LiteralPath $p.path
                $sid=New-Object System.Security.Principal.SecurityIdentifier($p.snapshot.sid)
                $rights = if ($p.write) {'Modify'} else {'ReadAndExecute'}
                $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,$rights,'ContainerInherit,ObjectInherit','None','Allow')
                $acl.AddAccessRule($rule)
                Set-Acl -LiteralPath $p.path -AclObject $acl
                $changedAcl=$true
                $afterAcl=Dacl $p.path
            }
            $args=@{Name=$p.name; Path=$p.path; Description=('LANBridgePro:'+$p.owner); FolderEnumerationMode='AccessBased'; CachingMode='None'}
            if ($p.write) {$args.ChangeAccess=@($p.account)} else {$args.ReadAccess=@($p.account)}
            New-SmbShare @args | Out-Null
            @{name=$p.name; owner=$p.owner; path=$p.path; dacl_after=(Dacl $p.path); dacl_before=$p.snapshot.dacl; grant_ntfs=[bool]$p.grant_ntfs}
            } catch {
                $original=$_.Exception.Message
                if ($changedAcl) {
                    try {Restore-Dacl $p.path $p.snapshot.dacl $afterAcl}
                    catch {throw ($original + ' NTFS rollback also needs attention: ' + $_.Exception.Message)}
                }
                throw $original
            }
            break
        }
        'share_remove' {
            $s=Share-Owned $p
            # Check before deleting a share; do not silently overwrite outside ACL changes.
            if ($p.grant_ntfs -and (Dacl $p.path) -ne $p.dacl_after) {throw 'NTFS ACL changed outside the app. Inspect permissions before rollback.'}
            Remove-SmbShare -Name $p.name -Force -Confirm:$false
            if ($p.grant_ntfs) {Restore-Dacl $p.path $p.dacl_before $p.dacl_after}
            @{removed=$p.name}
            break
        }
        'share_current' {
            $s=Share-Owned $p
            @{name=$s.Name; path=$s.Path; dacl=(Dacl $s.Path);
              access=@(Get-SmbShareAccess -Name $s.Name | Select-Object AccountName,AccessRight,AccessControlType)}
            break
        }
        'firewall_add' {
            $a=Adapter $p
            New-NetFirewallRule -Name $p.rule -DisplayName 'LANBridge Pro authorized SMB peer' -Description ('LANBridgePro:'+$p.owner) -PolicyStore PersistentStore -Direction Inbound -Action Allow -Profile Private -Protocol TCP -LocalPort 445 -RemoteAddress $p.peer -InterfaceAlias $a.Name -EdgeTraversalPolicy Block | Out-Null
            @{rule=$p.rule; owner=$p.owner; peer=$p.peer; interface=$a.Name}
            break
        }
        'firewall_remove' {
            $r=Get-NetFirewallRule -Name $p.rule -PolicyStore PersistentStore
            if ($r.Description -ne ('LANBridgePro:'+$p.owner)) {throw 'Firewall rule ownership does not match.'}
            $r | Remove-NetFirewallRule
            @{removed=$p.rule}
            break
        }
        'firewall_current' {
            $r=Get-NetFirewallRule -Name $p.rule -PolicyStore PersistentStore
            if ($r.Description -ne ('LANBridgePro:'+$p.owner)) {throw 'Firewall rule ownership does not match.'}
            @{name=$r.Name; description=$r.Description; enabled=[string]$r.Enabled; profile=[string]$r.Profile;
              remote=@($r | Get-NetFirewallAddressFilter | ForEach-Object {$_.RemoteAddress})}
            break
        }
        'diagnostics' {
            @{
                computer=$env:COMPUTERNAME;
                ipconfig=(Get-NetIPConfiguration | Format-List InterfaceAlias,InterfaceIndex,IPv4Address,IPv4DefaultGateway,DNSServer | Out-String);
                profiles=@(Get-NetConnectionProfile | Select-Object InterfaceAlias,InterfaceIndex,NetworkCategory,IPv4Connectivity);
                firewall=@(Get-NetFirewallProfile -PolicyStore ActiveStore | Select-Object Name,Enabled,DefaultInboundAction);
                client=(Get-SmbClientConfiguration | Select-Object RequireSecuritySignature,EnableInsecureGuestLogons,EnableSecuritySignature);
                server=(Get-SmbServerConfiguration | Select-Object EnableSMB1Protocol,EnableSMB2Protocol,RequireSecuritySignature);
                domain=(Get-CimInstance Win32_ComputerSystem | Select-Object PartOfDomain,Domain);
                connections=@(Get-SmbConnection -ErrorAction SilentlyContinue | Select-Object ServerName,ShareName,Dialect,Signed,Encrypted);
                services=@(Get-Service LanmanServer,LanmanWorkstation | Select-Object Name,Status)
            }
            break
        }
        default {throw 'Operation not allowed.'}
    }
    @{ok=$true;data=$data} | ConvertTo-Json -Depth 16 -Compress
} catch {
    @{ok=$false;error=$_.Exception.Message} | ConvertTo-Json -Depth 4 -Compress
    exit 1
}
